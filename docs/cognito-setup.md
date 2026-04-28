# Cognito Authentication Setup

## Overview

Amazon Cognito handles all authentication for the Virtual Portfolio Analyzer.
It manages user signup, login, email verification, and JWT token issuance.
No passwords are ever stored or handled by our application code —
Cognito owns the entire authentication lifecycle.

---

## User Pool: virtual-portfolio-users

### Configuration

| Setting | Value |
|---|---|
| Sign-in method | Email only |
| Self-registration | Enabled |
| Email verification | Required before login |
| MFA | Optional (Authenticator app) |
| Password minimum length | 8 characters |
| Password requirements | Uppercase + Lowercase + Number + Special char |
| Account recovery | Email only |

### Why These Choices

**Email only sign-in** — No username complexity, no phone costs.
Email is free, universally understood, and sufficient for our use case.

**Email verification required** — Proves the user owns the email before
they can access the app. Prevents fake accounts and ensures our
post-confirmation Lambda trigger fires only for legitimate users.

**MFA optional** — In a real financial app this would be required.
Optional here for learning purposes while still showing security awareness.

---

## App Client: virtual-portfolio-web-client

| Setting | Value | Reason |
|---|---|---|
| Client type | Public | React runs in browser — secrets can't be hidden |
| Client secret | None | Browser JS is readable by anyone — no secrets |
| Auth flow | ALLOW_USER_SRP_AUTH | Password never sent over network — only proof |
| Auth flow | ALLOW_REFRESH_TOKEN_AUTH | Silent token refresh — user stays logged in |
| Scopes | OpenID, Email, Profile | Standard claims included in JWT |

### Why Public Client and No Secret

React applications run entirely in the browser. Anyone can open
Chrome DevTools and read all JavaScript code and variables.
If you put a client secret in a React app it is immediately
exposed to the entire world.

Public client with SRP authentication is the correct and secure
approach for all browser-based applications.

---

## JWT Token Flow
User enters email + password on React login page
↓
AWS Amplify SDK sends credentials to Cognito
(SRP protocol — password never leaves the browser as plaintext)
↓
Cognito verifies credentials
↓
Cognito issues three tokens:
┌─────────────────────────────────────────────────────┐
│ ID Token (1 hour)                                   │
│ → Contains: email, userId (sub), name               │
│ → Used to identify WHO the user is                  │
├─────────────────────────────────────────────────────┤
│ Access Token (1 hour)                               │
│ → Sent with every API Gateway request               │
│ → Header: Authorization: Bearer eyJhbGc...         │
│ → API Gateway validates this on every call          │
├─────────────────────────────────────────────────────┤
│ Refresh Token (30 days)                             │
│ → Gets new ID + Access tokens silently              │
│ → User stays logged in without re-entering password │
└─────────────────────────────────────────────────────┘
↓
Every API call:
React → Authorization header → API Gateway
↓
Cognito Authorizer validates:
✅ Signature valid?
✅ Not expired?
✅ Belongs to our User Pool?
↓
Valid → Lambda triggered
userId extracted from token
DynamoDB queried for that userId only
↓
Invalid → 401 Unauthorized
React redirects to login

---

## Lambda Trigger: Post Confirmation

### What It Does

Every time a new user confirms their email, Cognito automatically
calls this Lambda function before the user can access the app.
The Lambda creates the user's record in DynamoDB with ₹10,000
virtual starting balance.

### Why Post Confirmation (not another trigger)

| Trigger | Why not used |
|---|---|
| Pre sign-up | Fires before email verified — account not legitimate yet |
| Post authentication | Fires on every login — would run repeatedly |
| Post confirmation | Fires exactly once after email verified ✅ |

### Exception Handling

Every possible failure is handled explicitly:

| Exception | Cause | Handling |
|---|---|---|
| MissingAttributeError | Cognito sent incomplete data | Log error, return event |
| ConditionalCheckFailedException | User already exists in DynamoDB | Skip silently — expected |
| ProvisionedThroughputExceededException | DynamoDB WCU exceeded | Log error with fix suggestion |
| ResourceNotFoundException | Wrong table name | Log error with env var hint |
| ValidationException | Wrong data format | Log full details |
| ClientError (other) | Unknown AWS error | Log code + message + HTTP status |
| Exception (catch-all) | Anything unexpected | Log type + message |

### Idempotency — Two Layers of Protection

**Layer 1 — Application level:**
Lambda checks if userId exists in DynamoDB before attempting write.
If exists → skip immediately.

**Layer 2 — Database level:**
put_item uses `ConditionExpression='attribute_not_exists(userId)'`
Even if two Lambda invocations pass Layer 1 simultaneously,
only one write succeeds at the database level.
The other gets ConditionalCheckFailedException — handled silently.

This makes the function safe to call any number of times
with identical results. This property is called idempotency.

### Why We Never Raise Exceptions

```python
# The function always ends with:
return event
```

Cognito requires the Lambda trigger to return the event object.
If the Lambda raises an exception or returns nothing,
Cognito blocks the signup — the user confirmed their email
but cannot access the app.

Our infrastructure failing should never punish the user.
All errors are logged to CloudWatch for investigation.
The user always gets through.

---

## DynamoDB Record Created on Signup

```json
{
    "userId":                "cognito-sub-uuid",
    "email":                 "user@gmail.com",
    "balance":               10000.00,
    "initialBalance":        10000.00,
    "totalInvested":         0.00,
    "totalCurrentValue":     0.00,
    "totalProfitLoss":       0.00,
    "returnPercent":         0.00,
    "createdAt":             "2026-04-27T10:00:00Z",
    "lastUpdated":           "2026-04-27T10:00:00Z",
    "initialBalanceGranted": true,
    "accountStatus":         "ACTIVE"
}
```

**Why Decimal not float for all number values:**
Float arithmetic has precision errors in Python.
`10000.10 + 0.20 = 10000.299999999999999`
Decimal is exact. Financial data must always use Decimal.
DynamoDB also rejects Python floats — Decimal is required.

---

## Key Values

These values are stored in `.env` locally and as Lambda
environment variables in AWS. They are never committed to GitHub.