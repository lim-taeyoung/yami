# models.py

from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# 사용자 모델
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)

# 엑셀 데이터 모델
class ExcelData(Base):
    __tablename__ = "excel_data"
    id = Column(Integer, primary_key=True, index=True)
    data = Column(String)
    sheet_name = Column(String, index=True)

# 게시글
class BoardMessage(Base):
    __tablename__ = "board_messages"
    id = Column(Integer, primary_key=True, index=True)
    user = Column(String)
    text = Column(Text)
    time = Column(String)
    image_filenames = Column(Text)

# 댓글
class BoardReply(Base):
    __tablename__ = "board_reply"
    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("board_messages.id"))
    user = Column(String)
    text = Column(String)
    time = Column(String)

# 매장 정보
class Store(Base):
    __tablename__ = "store"
    접점코드 = Column(String, primary_key=True, index=True)
    사번 = Column(String)
    이름 = Column(String)
    지사 = Column(String)
    센터 = Column(String)
    접점명 = Column(String)

# ✅ 사이트 설정 (타이틀 설정)
class SiteSettings(Base):
    __tablename__ = "site_settings"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
