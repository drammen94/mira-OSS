import json
import datetime
import uuid
from typing import Any, Dict


def to_json(obj: Any, indent: int = 2, **kwargs) -> str:
    return json.dumps(obj, indent=indent, default=_json_serializer, **kwargs)

def from_json(json_str: str) -> Dict[str, Any]:
    return json.loads(json_str)

def _json_serializer(obj: Any) -> Any:
    if hasattr(obj, 'to_dict'):
        return obj.to_dict()

    if isinstance(obj, datetime.datetime):
        return obj.isoformat()

    if isinstance(obj, datetime.date):
        return obj.isoformat()

    if isinstance(obj, uuid.UUID):
        return str(obj)

    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")