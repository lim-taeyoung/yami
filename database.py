import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# ✅ 환경변수에서 DATABASE_URL 가져오기
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./excel_data.db"
    
# ✅ PostgreSQL 연결 엔진 생성 (Render에서 SSL 필수)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# ✅ 세션 생성기 설정
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# ✅ 모델 베이스 클래스
Base = declarative_base()

# ✅ 의존성 주입용 DB 세션 함수
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ✅ 필요한 객체 export (import 편의를 위해)
__all__ = ["Base", "engine", "SessionLocal", "get_db"]
