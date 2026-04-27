\# Database Design — Virtual Portfolio Analyzer



This document outlines the NoSQL data architecture for the Virtual Portfolio Analyzer. The design is optimized for high-performance retrieval and cost-efficiency within the AWS Free Tier.



\## Architectural Rationale



While a Relational Database (RDS) could store this data, \*\*Amazon DynamoDB\*\* was selected to achieve a fully serverless architecture:

\- \*\*Zero Management:\*\* No instance patching, scaling, or maintenance.

\- \*\*Cost Efficiency:\*\* Utilizes \*\*Provisioned Capacity\*\* (1 RCU / 1 WCU) to stay within the AWS "Always Free" tier.

\- \*\*Performance:\*\* Predictable single-digit millisecond latency regardless of scale.

\- \*\*Event-Driven:\*\* Seamlessly integrates with AWS Lambda for real-time portfolio updates.



\## Data Access Patterns



The schema is designed specifically to support the following application queries:

1\. \*\*Get User Profile:\*\* Fetch balance and account metadata.

2\. \*\*Get Portfolio:\*\* Retrieve all active stock holdings for a specific user.

3\. \*\*Get Asset Detail:\*\* Fetch a specific stock record for trade validation.

4\. \*\*Audit History:\*\* Retrieve chronological trade logs for a specific user.



\---



\## 1. Users Table (`portfolio-users`)

\*Stores account state and virtual currency balance.\*



| Attribute | Type | Role | Description |

| :--- | :--- | :--- | :--- |

| `userId` | String | \*\*PK\*\* | Unique Identifier (Cognito Subject ID) |

| `balance` | Number | - | Current available virtual cash |

| `initialBalance`| Number | - | Default starting capital (e.g., 10,000) |

| `totalInvested` | Number | - | Current capital tied up in assets |

| `createdAt` | String | - | ISO-8601 account creation timestamp |



\*\*Configuration:\*\*

\- \*\*Partition Key:\*\* `userId`

\- \*\*Sort Key:\*\* None (Single item per user)



\---



\## 2. Portfolio Table (`portfolio-assets`)

\*Stores current active holdings. Items are created on 'BUY' and deleted when a position is closed.\*



| Attribute | Type | Role | Description |

| :--- | :--- | :--- | :--- |

| `userId` | String | \*\*PK\*\* | Unique Identifier (Cognito) |

| `stockSymbol` | String | \*\*SK\*\* | Ticker Symbol (e.g., TSLA, RELIANCE) |

| `quantity` | Number | - | Total shares held |

| `avgBuyPrice` | Number | - | Weighted average cost per share |

| `lastUpdated` | String | - | Timestamp of most recent trade |



\*\*Configuration:\*\*

\- \*\*Partition Key:\*\* `userId`

\- \*\*Sort Key:\*\* `stockSymbol`

\- \*\*Why?\*\* This composite key allows us to fetch an entire user's portfolio with a single `Query` operation on the `userId`.



\*\*Calculation Logic:\*\*

When buying more of an existing asset, the `avgBuyPrice` is updated:

`New Avg Price = (Total Cost of Existing Shares + Cost of New Shares) / Total Quantity`



\---



\## 3. Transactions Table (`portfolio-transactions`)

\*An immutable ledger of every trade executed.\*



| Attribute | Type | Role | Description |

| :--- | :--- | :--- | :--- |

| `userId` | String | \*\*PK\*\* | Unique Identifier (Cognito) |

| `timestamp` | String | \*\*SK\*\* | ISO-8601 (Ensures chronological ordering) |

| `type` | String | - | `BUY` or `SELL` |

| `stockSymbol` | String | - | Ticker involved |

| `quantity` | Number | - | Number of shares traded |

| `pricePerShare` | Number | - | Execution price |

| `totalAmount` | Number | - | Total trade value (Quantity \* Price) |



\*\*Configuration:\*\*

\- \*\*Partition Key:\*\* `userId`

\- \*\*Sort Key:\*\* `timestamp`

\- \*\*Why?\*\* DynamoDB stores items with the same PK physically together, sorted by the SK. This makes time-series retrieval (e.g., "Last 10 trades") extremely efficient.



\---



\## Data Integrity \& Transactional Logic



To ensure financial accuracy, the application uses \*\*DynamoDB Transactions (`TransactWriteItems`)\*\*. 



When a user executes a trade, the system performs an atomic operation:

1\. \*\*Update `portfolio-users`\*\*: Decrement/Increment the balance.

2\. \*\*Put/Update `portfolio-assets`\*\*: Adjust the quantity and average price.

3\. \*\*Put `portfolio-transactions`\*\*: Record the trade history.



If any single step fails (e.g., insufficient balance), the entire operation is rolled back, preventing data corruption.



\## Infrastructure Settings (Cost Optimization)

\- \*\*Capacity Mode:\*\* Provisioned

\- \*\*Read/Write Units:\*\* 1 RCU / 1 WCU per table

\- \*\*Auto-scaling:\*\* Disabled (Manual control for Free Tier safety)

\- \*\*TTL (Time to Live):\*\* Not enabled (Retaining full history for demo purposes)



