"""ผู้ใช้จำลองสำหรับ POC (แทน SSO/Keycloak)

ฝั่งหน้าเว็บส่ง header `X-Demo-User` เพื่อสลับบทบาทระหว่างสาธิต
ระบบจริงจะได้ user และ role จาก access token ของ OIDC แทน (SDD 7.1) โดยโค้ดส่วนอื่นไม่ต้องแก้
"""
from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class User:
    user_id: str
    name: str
    role: str  # PUB | STF | LGL | DIR | ADM
    title: str


DEMO_USERS: dict[str, User] = {
    u.user_id: u
    for u in [
        User("pub", "ประชาชนทั่วไป", "PUB", "ผู้ใช้ภายนอก"),
        User("stf1", "เจ้าหน้าที่ ก.", "STF", "เจ้าหน้าที่ · สำนักกำกับกิจการไฟฟ้า"),
        User("lgl1", "นิติกร ข.", "LGL", "เจ้าหน้าที่ฝ่ายคดี (คบ.)"),
        User("lgl2", "นิติกร ค.", "LGL", "เจ้าหน้าที่ฝ่ายคดี (คบ.)"),
        User("dir1", "ผอ.คบ. จ.", "DIR", "ผู้อำนวยการฝ่ายคดี (ผอ.คบ.)"),
        User("adm1", "ผู้ดูแลระบบ ง.", "ADM", "ผู้ดูแลระบบ"),
    ]
}


def current_user(x_demo_user: str | None = Header(default=None)) -> User:
    if x_demo_user is None:
        return DEMO_USERS["pub"]
    user = DEMO_USERS.get(x_demo_user)
    if user is None:
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "detail": "ไม่รู้จักผู้ใช้"})
    return user
