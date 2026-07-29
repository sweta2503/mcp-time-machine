"""
Shared fixture data — PaySim-flavoured, hardcoded, no ML needed.
This file is identical across all branches.
"""

TRANSACTIONS: dict = {
    "TXN001": {
        "id": "TXN001",
        "amount": 15_000.00,
        "merchant": "Casino Royale Vegas",
        "account_id": "ACC123",
        "flagged": True,
        "risk_score": 0.95,
        "timestamp": "2026-07-20T02:14:00Z",
        "type": "DEBIT",
    },
    "TXN002": {
        "id": "TXN002",
        "amount": 4.75,
        "merchant": "Starbucks #4821",
        "account_id": "ACC456",
        "flagged": False,
        "risk_score": 0.02,
        "timestamp": "2026-07-20T08:30:00Z",
        "type": "DEBIT",
    },
    "TXN003": {
        "id": "TXN003",
        "amount": 8_999.99,
        "merchant": "INTL WIRE TRANSFER",
        "account_id": "ACC789",
        "flagged": True,
        "risk_score": 0.88,
        "timestamp": "2026-07-21T23:59:00Z",
        "type": "TRANSFER",
    },
    "TXN004": {
        "id": "TXN004",
        "amount": 125.00,
        "merchant": "Amazon.com",
        "account_id": "ACC456",
        "flagged": False,
        "risk_score": 0.05,
        "timestamp": "2026-07-22T14:00:00Z",
        "type": "DEBIT",
    },
    "TXN005": {
        "id": "TXN005",
        "amount": 25_000.00,
        "merchant": "CryptoXchange Pro",
        "account_id": "ACC123",
        "flagged": True,
        "risk_score": 0.97,
        "timestamp": "2026-07-22T03:07:00Z",
        "type": "DEBIT",
    },
}

# Mutable — flag_account writes here
FLAGGED_ACCOUNTS: dict = {
    "ACC123": {
        "account_id": "ACC123",
        "holder": "Jordan Mercer",
        "flag_reason": "Multiple large overnight transactions",
        "flagged_at": "2026-07-21T00:00:00Z",
    },
    "ACC789": {
        "account_id": "ACC789",
        "holder": "Sam Okafor",
        "flag_reason": "Suspicious international wires",
        "flagged_at": "2026-07-22T00:00:00Z",
    },
}

ACCOUNT_HISTORY: dict = {
    "ACC123": [
        {"id": "TXN001", "amount": 15_000.00, "merchant": "Casino Royale Vegas"},
        {"id": "TXN005", "amount": 25_000.00, "merchant": "CryptoXchange Pro"},
        {"id": "TXN006", "amount": 9_500.00,  "merchant": "ATM Withdrawal"},
        {"id": "TXN007", "amount": 3_200.00,  "merchant": "Jewelry Plus"},
    ],
    "ACC456": [
        {"id": "TXN002", "amount": 4.75,   "merchant": "Starbucks #4821"},
        {"id": "TXN004", "amount": 125.00, "merchant": "Amazon.com"},
    ],
    "ACC789": [
        {"id": "TXN003", "amount": 8_999.99, "merchant": "INTL WIRE TRANSFER"},
        {"id": "TXN008", "amount": 7_500.00, "merchant": "INTL WIRE TRANSFER"},
        {"id": "TXN009", "amount": 12_000.00, "merchant": "INTL WIRE TRANSFER"},
    ],
}
