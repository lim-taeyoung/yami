from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import pandas as pd
from io import BytesIO

from database import get_db  # 🔁 본인 경로에 맞게 수정
from models import Store, StoreData  # 🔁 본인 경로에 맞게 수정
from schemas import StoreEntry  # 🔁 본인 경로에 맞게 수정

router = APIRouter()

@router.post("/store/sync")
async def sync_store_data(entry: StoreEntry, db: Session = Depends(get_db)):
    code = entry.접점코드.strip()
    center = entry.센터.strip()

    # 1️⃣ Store 테이블 처리
    store = db.query(Store).filter(Store.접점코드 == code, Store.센터 == center).first()
    if store:
        store.접점명 = entry.접점명.strip()
        store.이름 = entry.이름.strip()
        store.사번 = entry.사번.strip()
        store.지사 = entry.지사.strip()
        store.주소 = entry.주소.strip()
    else:
        store = Store(
            접점코드=code,
            접점명=entry.접점명.strip(),
            이름=entry.이름.strip(),
            사번=entry.사번.strip(),
            지사=entry.지사.strip(),
            센터=center,
            주소=entry.주소.strip(),
        )
        db.add(store)

    # 2️⃣ StoreData JSON 갱신
    entry_data = db.query(StoreData).order_by(StoreData.id.desc()).first()
    if not entry_data:
        return JSONResponse(content={"error": "❌ StoreData 비어있음"}, status_code=400)

    df = pd.read_json(BytesIO(entry_data.data.encode("utf-8")))
    df.columns = df.columns.str.strip()

    match = (df["접점코드"].astype(str).str.strip() == code) & \
            (df["센터"].astype(str).str.strip() == center)
    
    if match.any():
        df.loc[match, ["접점명", "이름", "사번", "지사", "주소"]] = [
            entry.접점명.strip(),
            entry.이름.strip(),
            entry.사번.strip(),
            entry.지사.strip(),
            entry.주소.strip(),
        ]
    else:
        new_row = {
            "접점코드": code,
            "접점명": entry.접점명.strip(),
            "이름": entry.이름.strip(),
            "사번": entry.사번.strip(),
            "지사": entry.지사.strip(),
            "센터": center,
            "주소": entry.주소.strip(),
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    entry_data.data = df.to_json(force_ascii=False, orient="records")
    db.commit()

    return JSONResponse(content={"message": "✅ 접점 정보가 저장되었습니다."}, status_code=200)
