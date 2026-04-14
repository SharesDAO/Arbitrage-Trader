import calendar
import time

import requests

from constants.constant import REQUEST_TIMEOUT, CONFIG


def get_user_transactions(status=1, start_index=0, num_of_transactions=300, sort_by_ascending=False, logger=None):
    """
    Get user transactions from SharesDAO API.

    Args:
        status: 1=Pending, 2=Processing, 3=Cancelled, 4=Executed
        start_index: Pagination offset
        num_of_transactions: Max results to return
        sort_by_ascending: Sort order
        logger: Optional logger

    Returns:
        List of transaction dicts from the API
    """
    # Import here to avoid circular dependency (sign_message needs CONFIG which is set at runtime)
    from util.crypto import sign_message

    url = "https://api.sharesdao.com:8443/transaction/user"

    timestamp = str(calendar.timegm(time.gmtime()))
    message = f"SharesDAO|Login|{timestamp}"
    signature = sign_message(CONFIG["DID_HEX"], message)

    payload = {
        "did_id": CONFIG["DID_HEX"],
        "timestamp": timestamp,
        "status": status,
        "start_index": start_index,
        "num_of_transactions": num_of_transactions,
        "sort_by_ascending": sort_by_ascending,
        "signature": signature
    }

    response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)

    if response.status_code == 200:
        transactions = response.json()
        if logger:
            logger.info(f"Retrieved {len(transactions)} transactions (status={status})")
        return transactions
    else:
        raise Exception(f"SharesDAO /transaction/user failed: {response.status_code} {response.text}")
