"""กฎสิทธิ์ ABAC (SDD 5.1 ง) — ตัดสินที่ server เท่านั้น

POC เขียนเป็นฟังก์ชัน Python ที่ทดสอบได้ตรง ๆ; ระบบจริงย้ายไปเป็น Rego บน OPA ได้ทีละกฎโดยคง signature เดิม
"""
from fastapi import HTTPException

from .auth import User

INTERNAL_ROLES = {"STF", "LGL", "DIR", "ADM"}


def can_read_law_version(user: User, status: str) -> bool:
    """R1: ทุกคนอ่านรุ่น approved ได้; รุ่น draft เห็นเฉพาะ LGL"""
    return status == "approved" or user.role == "LGL"


def can_read_case(user: User, classification: str, owners: list[str]) -> bool:
    """R2: เจ้าหน้าที่ภายใน + (internal หรือเป็นเจ้าของเรื่อง)"""
    if user.role not in INTERNAL_ROLES:
        return False
    if user.role == "DIR":  # ผอ.คบ. มอบหมายและกำกับทุกเรื่องของฝ่าย
        return True
    return classification == "internal" or user.user_id in owners


def can_edit_workflow(user: User, owners: list[str]) -> bool:
    """R4: เจ้าหน้าที่ฝ่ายคดีที่เป็นเจ้าของเรื่อง หรือ ผอ.คบ."""
    return (user.role == "LGL" and user.user_id in owners) or user.role == "DIR"


def can_assign(user: User) -> bool:
    """ผอ.คบ. มอบหมายเจ้าหน้าที่ผู้รับผิดชอบ (WI-EFL-04 ข้อ 5.1(2))"""
    return user.role == "DIR"


def can_register(user: User) -> bool:
    return user.role in {"STF", "LGL", "DIR"}


def search_filter_sql(user: User) -> tuple[str, dict]:
    """filter ที่ใส่ใน query ก่อนจัดอันดับ (ไม่กรองหลังได้ผล) — คืน (SQL fragment, params)"""
    if user.role not in INTERNAL_ROLES:
        return "(kind = 'provision')", {}
    if user.role == "DIR":
        return "(TRUE)", {}
    return (
        "(kind = 'provision' OR classification = 'internal' OR :uid = ANY(owners))",
        {"uid": user.user_id},
    )


def require_internal(user: User) -> None:
    if user.role not in INTERNAL_ROLES:
        # ผู้ใช้ภายนอกไม่ควรรู้ว่ามีโมดูลนี้ → ตอบเหมือนไม่มี
        raise HTTPException(404, detail={"code": "NOT_FOUND", "detail": "ไม่พบข้อมูล"})


def not_found() -> HTTPException:
    """ไม่มีสิทธิ์อ่าน = ตอบ 404 เหมือนไม่มีเรื่อง เพื่อไม่เปิดเผยว่าเรื่องนั้นมีอยู่"""
    return HTTPException(404, detail={"code": "NOT_FOUND", "detail": "ไม่พบข้อมูล"})


def forbidden(msg: str) -> HTTPException:
    return HTTPException(403, detail={"code": "FORBIDDEN_ACTION", "detail": msg})
