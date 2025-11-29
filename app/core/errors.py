from typing import Any, Dict, Optional

from fastapi import HTTPException, status


class APIError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.details = details


class NotFoundError(APIError):
    def __init__(self, message: str = "Not found", details: Optional[Dict[str, Any]] = None):
        super().__init__(status.HTTP_404_NOT_FOUND, "NOT_FOUND", message, details)


class ValidationError(APIError):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", message, details)


def error_content(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}
