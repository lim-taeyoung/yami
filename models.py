from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import Column, Integer, String, Text, ForeignKey
from database import Base

# SQLAlchemy 설정
SQLALCHEMY_DATABASE_URL = "sqlite:///./sales_data.db"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 사용자 모델 정의
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)

# 엑셀 데이터 저장 모델
class ExcelData(Base):
    __tablename__ = "excel_data"
    id = Column(Integer, primary_key=True, index=True)
    data = Column(String)  # JSON 형식으로 저장
    sheet_name = Column(String, index=True)  # 시트명 추가

class BoardMessage(Base):
    __tablename__ = "board_messages"

    id = Column(Integer, primary_key=True, index=True)
    user = Column(String)
    text = Column(Text)
    time = Column(String)
    image_filenames = Column(Text)  # ✅ 다중 이미지 저장 (JSON 문자열)

class BoardReply(Base):
    __tablename__ = "board_reply"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("board_messages.id"))  # ⚠️ 정확한 테이블명 확인
    user = Column(String)
    text = Column(String)
    time = Column(String)

class Store(Base):
    __tablename__ = "store"

    접점코드 = Column(String, primary_key=True, index=True)
    사번 = Column(String)
    이름 = Column(String)
    지사 = Column(String)
    센터 = Column(String)
    접점명 = Column(String)
    # 필요시 다른 컬럼도 추가 가능



# 데이터베이스 테이블 생성
Base.metadata.create_all(bind=engine)

# ✅ 데이터베이스 세션 생성 함수 (main.py 내부에 유지)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

