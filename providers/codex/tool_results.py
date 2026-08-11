"""Wire-format helpers for dynamic workspace tool results."""

import json
from typing import Any, Dict, List


def tool_result_content(value: Any) -> List[Dict[str, str]]:
    return [{
        "type": "inputText",
        "text": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    }]
