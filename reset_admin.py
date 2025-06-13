from sqlalchemy.orm import Session
from models import Base, User, engine

# 테이블 초기화
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

# 관리자 계정 생성
db = Session(bind=engine)
admin = User(
    username="admin",
    password="admin123",
    name="관리자",
    team1="총괄팀",
    team2="",
    level="1",
    role="admin",
    first_login=0
)
db.add(admin)
db.commit()
db.close()
