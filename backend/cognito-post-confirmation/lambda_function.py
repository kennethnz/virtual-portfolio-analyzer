"""
cognito-post-confirmation/lambda_function.py

PURPOSE:
    Triggered by Cognito after new user confirms email.
    Creates user record in DynamoDB with ₹10,000 balance.

EXCEPTIONS HANDLED:
    ConditionalCheckFailedException → user exists, skip silently
    ClientError (other)             → log error, don't block signup
    ValueError                      → invalid data, log and skip
    Exception                       → unexpected error, log and skip

IMPORTANT:
    Never raise exceptions in Cognito triggers.
    Always return the event object or signup breaks.
"""

import os
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import boto3
from botocore.exceptions import ClientError


# ─────────────────────────────────────────────────
# LOGGING
# Using logger instead of print for log levels
# ─────────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ─────────────────────────────────────────────────
# CUSTOM EXCEPTIONS
# Specific to our business logic
# ─────────────────────────────────────────────────
class MissingAttributeError(Exception):
    """Raised when required Cognito attributes are missing"""
    pass


class UserCreationError(Exception):
    """Raised when DynamoDB user creation fails unexpectedly"""
    pass


# ─────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────
INITIAL_BALANCE = Decimal('10000.00')
TRIGGER_SOURCE  = 'PostConfirmation_ConfirmSignUp'


# ─────────────────────────────────────────────────
# AWS CLIENT — outside handler for warm start reuse
# ─────────────────────────────────────────────────
try:
    dynamodb   = boto3.resource('dynamodb')
    TABLE_NAME = os.environ.get('USERS_TABLE', 'users-table')
    users_table = dynamodb.Table(TABLE_NAME)
    logger.info(f"DynamoDB client initialized for table: {TABLE_NAME}")

except Exception as e:
    # If client initialization fails the entire Lambda
    # is broken — log it clearly for debugging
    logger.error(f"Failed to initialize DynamoDB client: {str(e)}")
    raise


# ─────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# Breaking logic into small functions makes code:
# → Easier to test individually
# → Easier to read
# → Easier to reuse
# ─────────────────────────────────────────────────────────────────

def extract_user_data(event):
    """
    Safely extracts user data from Cognito event.

    Args:
        event: Cognito trigger event dict

    Returns:
        tuple: (user_id, email)

    Raises:
        MissingAttributeError: if required attributes missing
        KeyError: if event structure is unexpected
    """
    try:
        user_attributes = event['request']['userAttributes']
    except KeyError as e:
        raise MissingAttributeError(
            f"Unexpected event structure — missing key: {e}"
        )

    user_id = user_attributes.get('sub', '').strip()
    email   = user_attributes.get('email', '').strip()

    # Validate neither is empty
    if not user_id:
        raise MissingAttributeError("Missing 'sub' in userAttributes")
    if not email:
        raise MissingAttributeError("Missing 'email' in userAttributes")

    return user_id, email


def user_already_exists(user_id):
    """
    Checks if user already has a record in DynamoDB.

    Uses ProjectionExpression to fetch only the userId
    attribute — not the entire item. More efficient.

    Args:
        user_id: Cognito sub (userId)

    Returns:
        bool: True if exists, False if not

    Raises:
        ClientError: if DynamoDB call fails
    """
    try:
        response = users_table.get_item(
            Key={'userId': user_id},
            ProjectionExpression='userId'
        )
        exists = 'Item' in response
        logger.info(f"Existence check for {user_id}: {'exists' if exists else 'new user'}")
        return exists

    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_msg  = e.response['Error']['Message']
        logger.error(
            f"DynamoDB error checking existence for {user_id}: "
            f"{error_code} - {error_msg}"
        )
        # Re-raise so caller can decide what to do
        raise


def create_user_record(user_id, email):
    """
    Creates user record in DynamoDB with initial balance.
    Uses ConditionExpression for atomic idempotency.

    Args:
        user_id: Cognito sub
        email:   user's email address

    Returns:
        bool: True if created, False if already existed

    Raises:
        ClientError: if unexpected DynamoDB error
        UserCreationError: if creation fails for unknown reason
    """
    now = datetime.now(timezone.utc).isoformat()

    try:
        users_table.put_item(
            Item={
                # Primary key
                'userId':                   user_id,

                # User identity
                'email':                    email,

                # Virtual wallet
                'balance':                  INITIAL_BALANCE,
                'initialBalance':           INITIAL_BALANCE,

                # Portfolio summary (updated by buy/sell Lambdas)
                'totalInvested':            Decimal('0.00'),
                'totalCurrentValue':        Decimal('0.00'),
                'totalProfitLoss':          Decimal('0.00'),
                'returnPercent':            Decimal('0.00'),

                # Metadata
                'createdAt':                now,
                'lastUpdated':              now,
                'initialBalanceGranted':    True,
                'accountStatus':            'ACTIVE'
            },
            # Atomic guard — only write if userId doesn't exist
            # Catches race conditions at database level
            ConditionExpression='attribute_not_exists(userId)'
        )

        logger.info(
            f"✅ User record created: {user_id} | "
            f"email: {email} | "
            f"balance: ₹{INITIAL_BALANCE}"
        )
        return True

    except ClientError as e:
        error_code = e.response['Error']['Code']

        if error_code == 'ConditionalCheckFailedException':
            # User was created between our existence check
            # and this put_item — race condition caught
            # This is NOT an error — expected behavior
            logger.info(
                f"ConditionalCheck: user {user_id} already exists "
                f"(race condition caught at DB level) — skipping"
            )
            return False

        elif error_code == 'ProvisionedThroughputExceededException':
            logger.error(
                f"DynamoDB throughput exceeded creating user {user_id}. "
                f"Consider increasing WCU on users-table."
            )
            raise UserCreationError(
                f"Throughput exceeded: {error_code}"
            )

        elif error_code == 'ResourceNotFoundException':
            logger.error(
                f"Table '{TABLE_NAME}' not found. "
                f"Check USERS_TABLE environment variable."
            )
            raise UserCreationError(
                f"Table not found: {TABLE_NAME}"
            )

        elif error_code == 'ValidationException':
            logger.error(
                f"DynamoDB validation error for user {user_id}: "
                f"{e.response['Error']['Message']}"
            )
            raise UserCreationError(
                f"Validation error: {e.response['Error']['Message']}"
            )

        else:
            # Unknown ClientError — log everything for debugging
            logger.error(
                f"Unexpected ClientError creating user {user_id}: "
                f"Code={error_code} | "
                f"Message={e.response['Error']['Message']} | "
                f"HTTPStatus={e.response['ResponseMetadata']['HTTPStatusCode']}"
            )
            raise


# ─────────────────────────────────────────────────────────────────
# MAIN HANDLER
# ─────────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    """
    Entry point called by Cognito after email confirmation.

    NEVER raises exceptions — always returns event.
    User experience is never blocked by our infrastructure.
    """

    logger.info("=" * 50)
    logger.info("Post Confirmation trigger started")
    logger.info(f"Trigger source: {event.get('triggerSource')}")
    logger.info("=" * 50)

    # ── Guard: only process signup confirmations ──────────────
    trigger_source = event.get('triggerSource', '')

    if trigger_source != TRIGGER_SOURCE:
        logger.info(
            f"Ignoring trigger — expected '{TRIGGER_SOURCE}' "
            f"but got '{trigger_source}'"
        )
        return event

    # ── Extract user data ────────────────────────────────────
    try:
        user_id, email = extract_user_data(event)
        logger.info(f"Processing user: {email}")

    except MissingAttributeError as e:
        # Cognito sent us incomplete data — log it
        # Can't create user without userId and email
        logger.error(f"Cannot process signup — missing data: {str(e)}")
        logger.error(f"Full event: {event}")
        # Return event — don't block signup
        return event

    except Exception as e:
        logger.error(f"Unexpected error extracting user data: {str(e)}")
        return event

    # ── Idempotency check ────────────────────────────────────
    try:
        if user_already_exists(user_id):
            logger.info(f"User {user_id} already exists — nothing to do")
            return event

    except ClientError as e:
        # Can't check existence — DynamoDB is having issues
        # Proceed anyway and let ConditionExpression handle it
        logger.warning(
            f"Could not check existence for {user_id} — "
            f"proceeding with create attempt: "
            f"{e.response['Error']['Code']}"
        )

    # ── Create user record ───────────────────────────────────
    try:
        created = create_user_record(user_id, email)

        if created:
            logger.info(
                f"✅ Successfully onboarded new user: {email} "
                f"with ₹{INITIAL_BALANCE} virtual balance"
            )
        else:
            logger.info(f"User {user_id} already existed — no action needed")

    except UserCreationError as e:
        # Our custom exception — known failure case
        logger.error(
            f"UserCreationError for {user_id}: {str(e)} — "
            f"user will need balance granted manually or on first login"
        )

    except ClientError as e:
        # Unexpected AWS error
        logger.error(
            f"Unhandled ClientError for {user_id}: "
            f"{e.response['Error']['Code']} - "
            f"{e.response['Error']['Message']}"
        )

    except Exception as e:
        # Absolute safety net — catches anything we didn't anticipate
        logger.error(
            f"Completely unexpected error for {user_id}: "
            f"{type(e).__name__}: {str(e)}"
        )

    # ── Always return event ──────────────────────────────────
    logger.info("Post Confirmation trigger complete")
    return event