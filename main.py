import os
import uvicorn
import shutil
import json
from dotenv import load_dotenv
load_dotenv()
from io import BytesIO
from datetime import datetime
from typing import List, Optional
import pandas as pd
from pandas.api.types import is_float_dtype, is_numeric_dtype, is_datetime64_any_dtype
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query, Form, Request, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Boolean, ForeignKey, Text, DateTime
from sqlalchemy.orm import sessionmaker, Session
from database import Base
from models import ExcelData, BoardReply, Store, SiteSettings, SignupRequest
from database import get_db, engine 
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from settings import DATABASE_URL
from urllib.parse import quote
from datetime import datetime
from schemas import StoreEntry  # pydantic schema



app = FastAPI()
router = APIRouter()
templates = Jinja2Templates(directory="templates")
app.add_middleware(SessionMiddleware, secret_key="supersecret123!@#")
app.mount("/static", StaticFiles(directory="static"), name="static")
UPLOAD_PATH = "static/uploads"
MAIN_IMAGE_FILENAME = "main_banner.jpg"
NOTICE_FILE = "data/notices.json"

# ✅ 데이터베이스 설정


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)

# ✅ 데이터 모델 정의
class ExcelData(Base):
    __tablename__ = "excel_data"
    id = Column(Integer, primary_key=True, index=True)
    data = Column(String)  # JSON 형식으로 저장
    sheet_name = Column(String, index=True)  # 시트명 추가
    data_type = Column(String, index=True)  # 데이터 유형 추가

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    name = Column(String)
    team1 = Column(String)  # 지사
    team2 = Column(String)  # 센터
    level = Column(String)  # 직책
    role = Column(String, default="사용자")
    first_login = Column(Boolean, default=True)

class StoreData(Base):
    __tablename__ = "store_data"
    id = Column(Integer, primary_key=True, index=True)
    data = Column(String)


class BoardMessage(Base):
    __tablename__ = "board_messages"
    id = Column(Integer, primary_key=True, index=True)
    user = Column(String(100))
    text = Column(Text)
    time = Column(String(20))
    image_filenames = Column(Text)

class BoardReply(Base):
    __tablename__ = "board_reply"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("board_messages.id"))
    user = Column(String(100))
    text = Column(Text)
    time = Column(String(20))


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
    title = Column(String)
    notice = Column(Text)        # 공지사항 본문
    issue = Column(Text)         # 이슈사항 본문

class SignupRequest(Base):
    __tablename__ = "signup_requests"
    username = Column(String, primary_key=True)
    password = Column(String)
    name = Column(String)
    team1 = Column(String)
    team2 = Column(String)
    level = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    
Base.metadata.create_all(bind=engine)


# ✅ 데이터베이스 세션 생성 함수
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_code_to_user_mapping(db: Session):
    # ✅ StoreData에서 최신 접점관리 데이터 불러오기
    entry = db.query(StoreData).order_by(StoreData.id.desc()).first()
    if not entry:
        print("❌ 접점 관리 데이터가 없습니다.")
        return {}

    df = pd.read_json(BytesIO(entry.data.encode("utf-8")))
    
    # ✅ 접점코드, 사번, 이름 컬럼 확인
    if not all(col in df.columns for col in ["접점코드", "사번", "이름"]):
        print("❌ 접점 관리 데이터에 '접점코드', '사번', '이름' 컬럼이 누락되었습니다.")
        return {}

    df = df[["접점코드", "사번", "이름"]].dropna(subset=["접점코드"])
    df["접점코드"] = df["접점코드"].astype(str).str.strip().str.upper()
    df["사번"] = df["사번"].astype(str).str.strip()
    df["이름"] = df["이름"].astype(str).str.strip()

    # ✅ 사번이 없는 경우 제외, 중복 제거
    df = df[df["사번"] != ""]
    df = df.drop_duplicates(subset="접점코드", keep="first")

    # ✅ 딕셔너리 형태로 반환 (접점코드 -> {사번, 이름})
    code_map = df.set_index("접점코드")[["사번", "이름"]].to_dict(orient="index")
    print(f"✅ 매핑된 접점코드 수: {len(code_map)}")
    return code_map

def apply_user_mapping(df: pd.DataFrame, db: Session) -> pd.DataFrame:
    if "접점코드" not in df.columns:
        print("❌ 접점코드 컬럼이 없습니다. 매핑 불가.")
        return df

    df["접점코드"] = df["접점코드"].astype(str).str.strip().str.upper()
    code_map = get_code_to_user_mapping(db)
    
    if not code_map:
        print("❌ 접점코드 매핑 정보가 없습니다.")
        return df

    # ✅ 매핑된 정보 적용
    for idx, row in df.iterrows():
        접점코드 = row["접점코드"]
        if 접점코드 in code_map:
            if not row.get("사번"):  # 기존 사번이 없을 때만 적용
                df.at[idx, "사번"] = code_map[접점코드]["사번"]
            if not row.get("이름"):  # 기존 이름이 없을 때만 적용
                df.at[idx, "이름"] = code_map[접점코드]["이름"]

    print("✅ 사용자 매핑 적용 완료.")
    return df




# ✅ 로그인 페이지
@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/upload-main-image")
async def upload_main_image(main_image: UploadFile = File(...)):
    os.makedirs(UPLOAD_PATH, exist_ok=True)
    file_path = os.path.join(UPLOAD_PATH, MAIN_IMAGE_FILENAME)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(main_image.file, buffer)

    return RedirectResponse(url="/admin/users?username=admin", status_code=302)


# ✅ 🔑 로그인 처리 API
@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.username == username, User.password == password).first()

    if not user:
        return HTMLResponse(content="<p>⚠ 로그인 실패: 아이디 또는 비밀번호가 틀립니다.</p>", status_code=400)

    if user.first_login:
        request.session["temp_user"] = user.username
        return RedirectResponse(url="/change-password", status_code=303)

    request.session["username"] = user.username
    request.session["user_role"] = "admin" if user.role == "관리자" else "user"
    request.session["name"] = user.name

    return RedirectResponse(url=f"/main?username={user.username}", status_code=303)



@app.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request):
    username = request.session.get("temp_user")
    if not username:
        return RedirectResponse("/login")

    return templates.TemplateResponse("change-password.html", {"request": request, "username": username})

@app.post("/change-password")
async def update_password(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db)
):
    username = request.session.get("temp_user")
    if not username:
        return RedirectResponse("/login")

    # ✅ 길이 확인
    if not (6 <= len(new_password) <= 12):
        return HTMLResponse("<p>❌ 비밀번호는 6~12자리여야 합니다.</p>", status_code=400)

    # ✅ 비밀번호 확인 일치 여부 확인
    if new_password != confirm_password:
        return HTMLResponse("<p>❌ 비밀번호가 일치하지 않습니다. 다시 확인해주세요.</p>", status_code=400)

    user = db.query(User).filter(User.username == username).first()
    if not user:
        return HTMLResponse("<p>❌ 사용자 정보를 찾을 수 없습니다.</p>", status_code=400)

    user.password = new_password
    user.first_login = False
    db.commit()

    request.session.pop("temp_user", None)
    # ✅ 자바스크립트 팝업 후 리디렉션
    return HTMLResponse(
        content="""
        <script>
            alert("✅ 비밀번호 변경이 완료되었습니다.");
            window.location.href = "/";
        </script>
        """,
        status_code=200
    )


@app.get("/login-admin")
async def login_as_admin(request: Request):
    request.session["user_role"] = "admin"
    return RedirectResponse("/store", status_code=302)


@app.get("/admin/reset-password", response_class=HTMLResponse)
async def reset_password_form(request: Request):
    if request.session.get("user_role") != "admin":
        return HTMLResponse("<h3>⚠ 관리자만 접근 가능합니다.</h3>", status_code=403)
    return templates.TemplateResponse("admin/reset-password.html", {"request": request})

@app.post("/admin/reset-password")
async def admin_reset_password(
    request: Request,
    target_username: str = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_db)
):
    if request.session.get("user_role") != "admin":
        return HTMLResponse("<h3>⚠ 관리자만 사용할 수 있습니다.</h3>", status_code=403)

    user = db.query(User).filter(User.username == target_username).first()
    if not user:
        return HTMLResponse("<script>alert('❌ 사용자를 찾을 수 없습니다.'); history.back();</script>")

    if not (6 <= len(new_password) <= 12):
        return HTMLResponse("<script>alert('❌ 비밀번호는 6~12자리여야 합니다.'); history.back();</script>")

    user.password = new_password
    user.first_login = False  # ✅ 강제 변경이므로 최초로그인 상태도 해제
    db.commit()

    return HTMLResponse("<script>alert('✅ 비밀번호가 성공적으로 변경되었습니다.'); location.href='/admin/users';</script>")



# ✅ 사용자 리스트 보기 및 엑셀 업로드 버튼 추가
@app.get("/admin/users", response_class=HTMLResponse)
async def view_users(request: Request, db: Session = Depends(get_db)):
    if request.session.get("user_role") != "admin":
        return HTMLResponse("<h3>⚠ 관리자 전용 페이지입니다.</h3>", status_code=403)

    users = db.query(User).all()

    return templates.TemplateResponse("admin.html", {"request": request, "users": users})
    
@app.post("/admin/reset-data")
async def reset_excel_data(db: Session = Depends(get_db)):
    try:
        db.query(ExcelData).delete()
        db.commit()
        return HTMLResponse("<script>alert('✅ 실적 데이터 초기화 완료!'); location.href='/admin/users?username=admin';</script>")
    except Exception as e:
        return HTMLResponse(f"<script>alert('❌ 초기화 실패: {e}'); location.href='/admin/users?username=admin';</script>")

@app.post("/admin/reset-store")
async def reset_store_data(db: Session = Depends(get_db)):
    try:
        db.query(StoreData).delete()
        db.commit()
        return HTMLResponse("<script>alert('✅ 접점관리 데이터 초기화 완료!'); location.href='/admin/users?username=admin';</script>")
    except Exception as e:
        return HTMLResponse(f"<script>alert('❌ 초기화 실패: {e}'); location.href='/admin/users?username=admin';</script>")


# ✅ 엑셀 업로드로 사용자 계정 등록
@app.post("/admin/upload-users")
async def upload_users(file: UploadFile = File(...), db: Session = Depends(get_db)):
    df = pd.read_excel(file.file)

    required_columns = ["사번", "이름", "지사", "센터", "직책", "권한"]
    if not all(col in df.columns for col in required_columns):
        raise HTTPException(status_code=400, detail="❌ 엑셀에 필요한 컬럼이 없습니다: 사번, 이름, 지사, 센터, 직책, 권한")

    for _, row in df.iterrows():
        username = str(row["사번"]).strip()
        name = str(row["이름"]).strip()
        team1 = str(row["지사"]).strip()
        team2 = str(row["센터"]).strip()
        level = str(row["직책"]).strip()
        password = username  # ✅ 사번을 초기 비밀번호로 설정
        role = str(row["권한"]).strip()

        # 중복 방지
        if db.query(User).filter(User.username == username).first():
            print(f"🔁 이미 등록된 사용자: {username}, 건너뜀")
            continue

        db.add(User(
            username=username,
            password=password,
            name=name,
            team1=team1,
            team2=team2,
            level=level,
            role=role
        ))

    db.commit()
    return RedirectResponse(url="/admin/users?username=admin", status_code=303)



@app.get("/signup-request", response_class=HTMLResponse)
async def signup_request_page(request: Request):
    return templates.TemplateResponse("signup-request.html", {"request": request})


@app.post("/signup-request", response_class=HTMLResponse)
async def handle_signup_request(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    name: str = Form(...),
    team1: str = Form(...),
    team2: str = Form(...),
    level: str = Form(...),
    db: Session = Depends(get_db)
):
    if password != confirm_password:
        return HTMLResponse("<script>alert('❌ 비밀번호가 일치하지 않습니다.'); history.back();</script>")

    if not username.startswith("1") or len(username) != 7:
        return HTMLResponse("<script>alert('❌ 아이디는 1로 시작하는 7자리 사번이어야 합니다.'); history.back();</script>")

    if db.query(SignupRequest).filter_by(username=username).first():
        return HTMLResponse("<script>alert('⚠ 이미 신청된 아이디입니다.'); history.back();</script>")

    db.add(SignupRequest(
        username=username,
        password=password,
        name=name,
        team1=team1,
        team2=team2,
        level=level,
    ))
    db.commit()

    return HTMLResponse("<script>alert('✅ 아이디 신청이 완료되었습니다. 관리자 승인 후 사용 가능합니다.'); location.href='/';</script>")


@app.get("/admin/signup-requests", response_class=HTMLResponse)
async def view_signup_requests(request: Request, db: Session = Depends(get_db)):
    requests = db.query(SignupRequest).all()
    return templates.TemplateResponse("admin/signup-requests.html", {
        "request": request,
        "signup_requests": requests
    })

@app.post("/admin/approve-multiple-signups")
async def approve_multiple_signups(
    request: Request,
    usernames: List[str] = Form(...),
    db: Session = Depends(get_db)
):
    for username in usernames:
        req = db.query(SignupRequest).filter(SignupRequest.username == username).first()
        if req:
            db.add(User(
                username=req.username,
                password=req.password,
                name=req.name,
                team1=req.team1,
                team2=req.team2,
                level=req.level,
                role="사용자"
            ))
            db.delete(req)
    db.commit()
    return RedirectResponse("/admin/signup-requests", status_code=303)



@app.post("/admin/reject-multiple-signups")
async def reject_multiple_signups(
    request: Request,
    usernames: List[str] = Form(None),
    db: Session = Depends(get_db)
):
    if not usernames:
        return HTMLResponse("<script>alert('❌ 반려할 사용자를 선택해주세요.'); history.back();</script>")

    for username in usernames:
        req = db.query(SignupRequest).filter(SignupRequest.username == username).first()
        if req:
            db.delete(req)

    db.commit()
    return HTMLResponse("<script>alert('❌ 선택된 신청건이 반려되었습니다.'); location.href='/admin/signup-requests';</script>")


@app.get("/main", response_class=HTMLResponse)
def main_page(request: Request, username: str = Query("사용자"), mode: str = Query("mobile"), db: Session = Depends(get_db)):
    name = request.session.get("name", "사용자")

    # ✅ 대문 이미지 경로
    image_path = "static/uploads/main_banner.jpg"
    image_url = f"/{image_path}" if os.path.exists(image_path) else None

    # ✅ 사이트 설정 데이터 조회
    setting = db.query(SiteSettings).first()
    title_text = setting.title if setting else "업데이트된 타이틀이 없습니다."
    notice_text = setting.notice if setting else "공지사항이 없습니다."
    issue_text = setting.issue if setting else "이슈사항이 없습니다."

    return templates.TemplateResponse("main.html", {
        "request": request,
        "username": username,
        "mode": mode,
        "name": name,
        "main_image_url": image_url,
        "title_text": title_text,
        "notice_text": notice_text,
        "issue_text": issue_text,
    })


# ✅ 엑셀 업로드 페이지
@app.get("/upload", response_class=HTMLResponse)
async def upload_page():
    return """
    <html>
    <head>
        <style>
            .upload-container { text-align: center; margin-top: 50px; }
            .upload-form { display: flex; flex-direction: column; align-items: center; gap: 10px; }
            .success-message { color: green; font-weight: bold; margin-top: 20px; }
        </style>
        <script>
            function showSuccessMessage() {
                document.getElementById("success").innerHTML = ✅ 종합 데이터 업로드 성공!;
            }
        </script>
    </head>
    <body>
        <div class="upload-container">
            <h1>엑셀 업로드</h1>
            <form class="upload-form" method="post" enctype="multipart/form-data" action="/upload" onsubmit="showSuccessMessage()">
                <input type="file" name="file" accept=".xlsx" required>
                <button type="submit">📂 업로드</button>
            </form>
            <p id="success" class="success-message"></p>
            <br>
             <a href="/main"><button type="button">🏠 메인으로 이동</button></a>
        </div>
    </body>
    </html>
    """
from datetime import datetime


@app.post("/upload", response_class=HTMLResponse)
async def upload_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    xls = pd.ExcelFile(file.file)

    for sheet_name in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet_name)

        for col in df.columns:
            col_data = df[col]

            # ✅ 1. 퍼센트 처리 (0~1 float → "xx%")
            if pd.api.types.is_float_dtype(col_data) and col_data.between(0, 1).all():
                df[col] = (col_data * 100).round(1).astype(str) + "%"

            # ✅ 2. 날짜 처리 (datetime 또는 object → "MM/DD")
            elif pd.api.types.is_datetime64_any_dtype(col_data):
                df[col] = col_data.dt.strftime("%m/%d")
            elif pd.api.types.is_object_dtype(col_data):
                parsed = pd.to_datetime(col_data, errors='coerce')
                if parsed.notna().sum() > 0:
                    df[col] = parsed.dt.strftime("%m/%d")

            # ✅ 3. 자연수 float → int (단 NaN 보존)
            if pd.api.types.is_float_dtype(col_data):
                if (col_data.dropna() % 1 == 0).all():
                    df[col] = df[col].apply(lambda x: int(x) if pd.notna(x) else "")

        # ✅ 4. NaN → 빈칸
        df = df.fillna("")

        # ✅ 5. DB 저장
        db_data = ExcelData(
            data=df.to_json(orient="records", force_ascii=False),
            sheet_name=sheet_name,
            data_type="종합"  # 🔥 꼭 있어야 검색됨
        )
        db.add(db_data)

    db.commit()

    # ✅ 완료 메시지
    raw_page = await upload_page()
    html_str = raw_page.replace(
        '<p id="success" class="success-message"></p>',
        '<p id="success" class="success-message">✅ 데이터 업로드 성공!</p>'
    )
    return HTMLResponse(content=html_str)


# ✅ 아래 코드는 기존 /dashboard 엔드포인트에 컬럼 선택 필터 기능을 추가하고,
# ✅ 선택된 체크박스를 유지되도록 개선한 버전입니다.
# ✅ 버튼 추가: 메인화면 이동, 체크 모두 해제 기능


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    type: str = Query("종합"),
    search_column: str = Query("사번"),
    search_value: str = Query(None),
    columns: list[str] = Query(None),
    sort_column: str = Query("일반후불"),
    sort_order: str = Query("desc"),
    mode: str = Query("mobile"),
    db: Session = Depends(get_db)
):
    if columns is None or not columns:
        columns = ["일반후불", "MNP", "유선신규 I+T", "MIT(I) 합계", "신동률"]

    search_value = (search_value or "").strip()

    available_types = db.query(ExcelData.data_type).distinct().all()
    available_types = [t[0] for t in available_types]
    if type not in available_types:
        return HTMLResponse(content=f"<p>⚠ 데이터 유형 '{type}'이(가) 없습니다.</p>")

    sheet_names = db.query(ExcelData.sheet_name).filter(ExcelData.data_type == type).distinct().all()
    sheet = sheet_names[0][0] if sheet_names else None

    latest_data = db.query(ExcelData).filter(
        ExcelData.data_type == type, ExcelData.sheet_name == sheet
    ).order_by(ExcelData.id.desc()).first()

    if not latest_data:
        return HTMLResponse(content=f"<p>📌 {type} 데이터 ({sheet})가 없습니다.</p>")

    df = pd.read_json(BytesIO(latest_data.data.encode("utf-8")))
    df.columns = df.columns.str.strip()

    df = apply_user_mapping(df, db)

    if search_column not in df.columns:
        return HTMLResponse(content=f"<p>⚠ '{search_column}' 컨텐츠가 없습니다.</p>")

    base_columns = [col for col in ["사번", "이름", "지사", "센터", "접점코드", "접점명"] if col in df.columns]

    mapped_columns = {
        "일반후불(M,대)": ["M-3 무선(M)", "M-2 무선(M)", "M-1 무선(M)", "M-3 무선(대)", "M-2 무선(대)", "M-1 무선(대)"],
        "유선신규(M,대)": ["M-3 유선신규(M)", "M-2 유선신규(M)", "M-1 유선신규(M)", "M-3 유선신규(대)", "M-2 유선신규(대)", "M-1 유선신규(대)"]
    }

    expanded_columns = []
    for col in columns:
        expanded_columns.extend(mapped_columns.get(col, [col]))

    selected_columns = base_columns + [col for col in expanded_columns if col in df.columns and col not in base_columns]
    df = df[[col for col in selected_columns if col in df.columns]]

    if not search_value:
        table_html = ""
    else:
        try:
            df_filtered = df[df[search_column].astype(str).str.strip() == search_value]
        except Exception as e:
            return HTMLResponse(content=f"<p style='color:red;'>🔥 필터링 중 오류 발생: {e}</p>")

        if df_filtered.empty:
            return HTMLResponse(content="<p style='color:red; font-weight:bold; font-size:50px;'>❌ 검색 결과가 없습니다.</p>")

        if sort_column in df_filtered.columns:
            try:
                df_filtered[sort_column] = pd.to_numeric(df_filtered[sort_column], errors='coerce')
                df_filtered = df_filtered.sort_values(by=sort_column, ascending=(sort_order == "asc"))
            except Exception as e:
                print(f"⚠ 정렬 오류: {e}")

        sum_cols = [
            "일반후불", "010", "MNP", "기변", "중고", "5G", "3G/LTE", "100K이상", "초이스4종",
            "유선신규 I+T", "유선신규 I", "유선신규 T", "유선약갱 I+T", "유선약갱 I", "유선약갱 T",
            "MIT(M) 합계", "MIT(M) 신규", "MIT(M) 약갱", "MIT(I) 합계", "MIT(I) 신규", "MIT(I) 약갱",
            "신동", "S25", "AIP16", "신동모수",
            "M-3 무선(M)", "M-2 무선(M)", "M-1 무선(M)", "M-3 무선(대)", "M-2 무선(대)", "M-1 무선(대)",
            "M-3 유선신규(M)", "M-2 유선신규(M)", "M-1 유선신규(M)", "M-3 유선신규(대)", "M-2 유선신규(대)", "M-1 유선신규(대)"
        ]

        df_base = pd.read_json(BytesIO(latest_data.data.encode("utf-8")))
        df_base.columns = df_base.columns.str.strip()
        df_base = apply_user_mapping(df_base, db)
        df_calc = df_base[df_base[search_column].astype(str).str.strip() == search_value].copy()
        df_calc["신동"] = pd.to_numeric(df_calc.get("신동", pd.Series([0]*len(df_calc))), errors='coerce').fillna(0)
        df_calc["신동모수"] = pd.to_numeric(df_calc.get("신동모수", pd.Series([0]*len(df_calc))), errors='coerce').fillna(0)

        sum_신동 = df_calc["신동"].sum()
        sum_신동모수 = df_calc["신동모수"].sum()
        ratio = f"{round((sum_신동 / sum_신동모수) * 100, 1)}%" if sum_신동모수 > 0 else "--"

        summary = {}
        for col in df_filtered.columns:
            if col in base_columns:
                summary[col] = ""
            elif col in sum_cols:
                try:
                    value = pd.to_numeric(df_filtered[col], errors='coerce').sum(skipna=True)
                    summary[col] = int(value) if pd.notnull(value) else 0
                except:
                    summary[col] = 0
            else:
                summary[col] = ""

        if "신동률" in columns:
            summary["신동률"] = ratio

        df_result = pd.concat([pd.DataFrame([summary]), df_filtered], ignore_index=True)

        if "접점코드" in df_result.columns and "센터" in df_result.columns:
            df_result["접점코드"] = df_result.apply(
                lambda row: f'<a href="/report?code={row["접점코드"]}&center={row["센터"]}" target="_blank">{row["접점코드"]}</a>',
                axis=1
            )

        # 기본 생성
        table_html = df_result.to_html(classes="table table-striped", index=False, escape=False)

        # <th>에 class 추가
        table_html = table_html.replace('<th>사번</th>', '<th class="sabun-col hidden-col">사번</th>')
        table_html = table_html.replace('<th>이름</th>', '<th class="name-col hidden-col">이름</th>')

        # <td>에 class 추가 (텍스트 기준 치환)
        if "사번" in df_result.columns:
            for val in df_result["사번"].astype(str).unique():
                if val.strip() == "":
                    continue
                table_html = table_html.replace(f"<td>{val}</td>", f'<td class="sabun-col hidden-col">{val}</td>')

        if "이름" in df_result.columns:
            for val in df_result["이름"].astype(str).unique():
                if val.strip() == "":
                    continue
                table_html = table_html.replace(f"<td>{val}</td>", f'<td class="name-col hidden-col">{val}</td>')



    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "type": type,
        "search_column": search_column,
        "search_value": search_value,
        "columns": columns,
        "table_html": table_html,
        "sort_column": sort_column,
        "sort_order": sort_order,
        "mode": mode
    })



# ✅ 한마디 게시판 메인 페이지


# ✅ 메시지 불러오기
@app.get("/board", response_class=HTMLResponse)
async def board_page(
    request: Request,
    page: int = 1,
    db: Session = Depends(get_db)
):
    PAGE_SIZE = 15
    total_count = db.query(BoardMessage).count()
    total_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(1, min(page, total_pages))

    offset = (page - 1) * PAGE_SIZE
    messages = db.query(BoardMessage)\
        .order_by(BoardMessage.id.desc())\
        .offset(offset)\
        .limit(PAGE_SIZE)\
        .all()

    for msg in messages:
        msg.image_list = json.loads(msg.image_filenames) if msg.image_filenames else []
        msg.replies = db.query(BoardReply).filter_by(message_id=msg.id).all()

    # ✅ 로그인 사용자 정보 가져오기
    username = request.session.get("username")
    user = db.query(User).filter(User.username == username).first() if username else None

    return templates.TemplateResponse("board.html", {
        "request": request,
        "messages": messages,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "user": user  # ✅ user 객체 템플릿에 넘김
    })

# ✅ 게시글 등록 (최대 이미지 5장)
@app.post("/board/message")
async def post_message(
    request: Request,
    user: str = Form(...),
    message: str = Form(...),
    images: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db)
    
):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    saved_filenames = []

    os.makedirs("static/board_images", exist_ok=True)

    for image in images[:5]:
        if image.filename:
            clean_time = now.replace(":", "-").replace(" ", "_")
            safe_name = f"{clean_time}_{image.filename}"
            path = os.path.join("static/board_images", safe_name)
            with open(path, "wb") as buffer:
                shutil.copyfileobj(image.file, buffer)
            saved_filenames.append(safe_name)

    db.add(BoardMessage(
        user=user,
        text=message,
        time=now,
        image_filenames=json.dumps(saved_filenames)
    ))
    db.commit()
    return RedirectResponse(url="/board", status_code=303)

# ✅ 게시글 삭제 + 이미지 삭제
@app.post("/board/delete")
async def delete_message(msg_id: int = Form(...), db: Session = Depends(get_db)):
    message = db.query(BoardMessage).filter(BoardMessage.id == msg_id).first()
    if message:
        if message.image_filenames:
            for filename in json.loads(message.image_filenames):
                try:
                    os.remove(os.path.join("static/board_images", filename))
                except:
                    pass
        db.delete(message)
        db.commit()
    return RedirectResponse(url="/board", status_code=303)


# ✅ 댓글 등록
@app.post("/board/reply")
async def post_reply(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    message_id_raw = form.get("message_id")
    user = form.get("user")
    reply = form.get("reply")

    if not message_id_raw or not user or not reply:
        return HTMLResponse(
            f"<h3>❌ 댓글 등록 실패: 필수 입력 누락</h3>",
            status_code=400
        )

    try:
        message_id = int(message_id_raw)
    except Exception as e:
        return HTMLResponse(f"<h3>❌ 댓글 등록 실패: {e}</h3>", status_code=400)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.add(BoardReply(
        message_id=message_id,
        user=user,
        text=reply,
        time=now
    ))
    db.commit()
    return RedirectResponse(url="/board", status_code=303)

COLUMN_MAPPING = {
    # 기본 정보
    "접점코드": "접점코드",
    "접점명": "접점명",
    "지사": "지사",
    "센터": "센터",
    "사번": "담당사번",  # or "사번" if 그대로 사용
    "이름": "담당자",
    "주소": "주소",

    # 무선 실적
    "일반후불": "일반후불",
    "010": "010",
    "MNP": "MNP",
    "기변": "기변",
    "5G": "5G",
    "3G/LTE": "3G/LTE",
    "중고": "중고",
    "2nd": "2nd",
    "초이스4종": "초이스4종",
    "100K이상": "100K이상",
    "S25 류": "S25",
    "AIP16 류": "AIP16",

    # 유선 실적
    "신규 I+T": "유선신규 I+T",
    "약갱 I+T": "유선약갱 I+T",
    "MIT(M) 합계": "MIT(M) 합계",
    "MIT(I) 합계": "MIT(I) 합계",
    "MIT(M) 신규": "MIT(M) 신규",
    "MIT(I) 신규": "MIT(I) 신규",
    "MIT(M) 약갱": "MIT(M) 약갱",
    "MIT(I) 약갱": "MIT(I) 약갱",
    "신규 I": "유선신규 I",
    "신규 T": "유선신규 T",
    "약갱 I": "유선약갱 I",
    "약갱 T": "유선약갱 T",

    # 월별 실적
    "12월 무선 M": "M-3 무선(M)",
    "1월 무선 M": "M-2 무선(M)",
    "2월 무선 M": "M-1 무선(M)",
    "12월 유선 M": "M-3 유선신규(M)",
    "1월 유선 M": "M-2 유선신규(M)",
    "2월 유선 M": "M-1 유선신규(M)",
    "12월 무선 대": "M-3 무선(대)",
    "1월 무선 대": "M-2 무선(대)",
    "2월 무선 대": "M-1 무선(대)",
    "12월 유선 대": "M-3 유선신규(대)",
    "1월 유선 대": "M-2 유선신규(대)",
    "2월 유선 대": "M-1 유선신규(대)",
}

# ✅ 엑셀 표 레이아웃을 기준으로 /report 페이지 재정비
from fastapi import Query
from starlette.responses import HTMLResponse

from fastapi import Query

@app.get("/report", response_class=HTMLResponse)
async def render_report(
    request: Request,
    code: str = Query(...),
    center: str = Query(...),
    db: Session = Depends(get_db)
):
    # ✅ 종합현황 데이터 불러오기
    data_entry = db.query(ExcelData).filter(
        ExcelData.sheet_name == "종합현황"
    ).order_by(ExcelData.id.desc()).first()

    if not data_entry:
        return HTMLResponse("<h3>❌ 종합현황 데이터가 없습니다.</h3>")

    df = pd.read_json(BytesIO(data_entry.data.encode("utf-8")))
    df["접점코드"] = df["접점코드"].astype(str).str.strip().str.upper()
    df["센터"] = df["센터"].astype(str).str.strip()

    # ✅ 접점코드 + 센터명 기준으로 정확히 찾기
    filtered_df = df[(df["접점코드"] == code.strip().upper()) & (df["센터"] == center.strip())]
    if filtered_df.empty:
        return HTMLResponse("<h3>❌ 해당 접점코드와 센터명을 가진 데이터가 없습니다.</h3>", status_code=404)

    row = filtered_df.iloc[0]

    def get(col):
        return row[col] if col in row else "-"

    # ✅ 사번/이름 매핑 (Store 테이블 기준)
    code_map = get_code_to_user_mapping(db)
    user_info = code_map.get(code.strip().upper(), {"사번": "-", "이름": "-"})

    # ✅ 접점별 판매모델 불러오기
    model_entry = db.query(ExcelData).filter(
        ExcelData.sheet_name == "접점별 판매모델"
    ).order_by(ExcelData.id.desc()).first()

    model_data = []
    if model_entry:
        model_df = pd.read_json(BytesIO(model_entry.data.encode("utf-8")))
        model_df["접점코드"] = model_df["접점코드"].astype(str).str.strip().str.upper()
        model_df = model_df[model_df["접점코드"] == code.strip().upper()]
        if not model_df.empty:
            model_df = model_df[["모델", "합계", "010", "MNP", "기변"]].fillna(0)
            model_df = model_df.groupby("모델", as_index=False).sum(numeric_only=True)
            model_df = model_df.sort_values(by="합계", ascending=False).head(8)
            model_data = model_df.to_dict(orient="records")

    return templates.TemplateResponse("report.html", {
        "request": request,
        "get": get,
        "model_data": model_data,   
        "user_info": user_info
    })

# ✅ 접점코드 입력 페이지 추가
@app.get("/report-search", response_class=HTMLResponse)
async def report_search_page(request: Request):
    return templates.TemplateResponse("report-search.html", {"request": request})


@app.get("/partner-store", response_class=HTMLResponse)
async def partner_store_page(
    request: Request,
    filter_column: str = Query("지사"),
    filter_value: str = Query(""),
    column_filter: list[str] = Query(default=[]),
    db: Session = Depends(get_db)
):
    data_entry = db.query(ExcelData).filter(
        ExcelData.sheet_name == "파트너매장"
    ).order_by(ExcelData.id.desc()).first()

    if not data_entry:
        return HTMLResponse("<h3>❌ 파트너매장 시트 데이터가 없습니다.</h3>")

    df = pd.read_json(BytesIO(data_entry.data.encode("utf-8")))

    if "접점코드" in df.columns:
        code_map = get_code_to_user_mapping(db)
        df["사번"] = df.get("사번", "")
        df["이름"] = df.get("이름", "")
        mapped = df["접점코드"].map(code_map).dropna()
        mapped_df = mapped.apply(pd.Series)
        for idx in mapped_df.index:
            if pd.isna(df.at[idx, "사번"]) or df.at[idx, "사번"] == "":
                df.at[idx, "사번"] = mapped_df.at[idx, "사번"]
            if pd.isna(df.at[idx, "이름"]) or df.at[idx, "이름"] == "":
                df.at[idx, "이름"] = mapped_df.at[idx, "이름"]

    def convert_percent(x):
        if pd.isnull(x):
            return "--"
        try:
            x = str(x).strip()
            return f"{round(float(x.replace('%','')) if '%' in x else float(x)*100, 1)}%"
        except:
            return str(x)

    fixed_columns = ["사번", "이름", "지사", "센터", "접점코드", "접점명"]
    all_columns = df.columns.tolist()

    if column_filter:
        col_map = {
            "실적확인": ["목표", "무선", "MIT", "MIT(실적인정)", "달성률", "MNP", "신동(개통)", "신동률(개통)"],
            "파트너 정보": ["최근1년미달", "지원금", "계약시작", "계약종료", "잔여계약일"],
            "모두보기": all_columns
        }
        selected_cols = []
        for key in column_filter:
            selected_cols.extend(col_map.get(key, []))
        selected_cols = list(dict.fromkeys(fixed_columns + selected_cols))
    else:
        selected_cols = fixed_columns

    selected_cols = [col for col in selected_cols if col in df.columns]
    df = df[selected_cols]

    if not filter_value:
        table_html = df.head(0).to_html(classes="table table-striped", index=False, escape=False)
    else:
        df_filtered = df[df[filter_column].astype(str).str.strip() == filter_value]
        if df_filtered.empty:
            return HTMLResponse("<p style='color:red; font-size:24px;'>❌ 검색 결과가 없습니다.</p>")

        sum_cols = ["목표", "무선", "MIT", "MIT(실적인정)", "MNP", "신동(개통)", "지원금"]
        base_cols = ["사번", "이름", "지사", "센터", "접점코드", "접점명"]

        summary = {}
        for col in df_filtered.columns:
            if col in base_cols:
                summary[col] = "합계" if col == "사번" else ""
            elif col in sum_cols:
                try:
                    value = pd.to_numeric(df_filtered[col], errors='coerce').sum()
                    summary[col] = int(value) if pd.notnull(value) else 0
                except:
                    summary[col] = 0
            else:
                summary[col] = ""

        if "달성률" in df_filtered.columns:
            try:
                목표 = float(summary.get("목표", 0))
                무선 = float(summary.get("무선", 0))
                mit = float(summary.get("MIT(실적인정)", 0))
                summary["달성률"] = f"{round((무선 + mit) / 목표 * 100, 1)}%" if 목표 > 0 else "--"
                df_filtered["달성률"] = df_filtered["달성률"].apply(convert_percent)
            except:
                summary["달성률"] = "--"

        if "신동률(개통)" in df_filtered.columns:
            try:
                신동 = float(summary.get("신동(개통)", 0))
                mnp = float(summary.get("MNP", 0))
                summary["신동률(개통)"] = f"{round(신동 / mnp * 100, 1)}%" if mnp > 0 else "--"
                df_filtered["신동률(개통)"] = df_filtered["신동률(개통)"].apply(convert_percent)
            except:
                summary["신동률(개통)"] = "--"

        # ✅ 강제 2칸 shift 방식
        # ✅ 강제 왼쪽으로 2칸 이동
        summary_df = pd.DataFrame([summary])
        cols = summary_df.columns.tolist()
        shifted = pd.DataFrame(columns=cols)

        for i in range(len(cols)):
            src_index = i + 2
            val = summary_df.iloc[0, src_index] if src_index < len(cols) else ""
            shifted.at[0, cols[i]] = val

        summary_df = shifted.copy()
        df_result = pd.concat([summary_df, df_filtered], ignore_index=True)

        if "접점코드" in df_result.columns and "센터" in df_result.columns:
            df_result["접점코드"] = df_result.apply(
                lambda row: f'<a href="/report?code={row["접점코드"]}&center={row["센터"]}" target="_blank">{row["접점코드"]}</a>',
                axis=1
            )

        table_html = df_result.to_html(classes="table table-striped", index=False, escape=False)
        table_html = table_html.replace("<tr>", '<tr class="sum-row">', 1)

        table_html = table_html.replace('<th>사번</th>', '<th class="sabun-col hidden-col">사번</th>')
        table_html = table_html.replace('<th>이름</th>', '<th class="name-col hidden-col">이름</th>')

        if "사번" in df_result.columns:
            for val in df_result["사번"].astype(str).unique():
                val = val.strip()
                if val == "" or val == "합계":
                    continue
                table_html = table_html.replace(f"<td>{val}</td>", f'<td class="sabun-col hidden-col">{val}</td>')

        if "이름" in df_result.columns:
            for val in df_result["이름"].astype(str).unique():
                val = val.strip()
                if val == "":
                    continue
                table_html = table_html.replace(f"<td>{val}</td>", f'<td class="name-col hidden-col">{val}</td>')

    return templates.TemplateResponse("partner-store.html", {
        "request": request,
        "table_html": table_html,
        "filter_column": filter_column,
        "filter_value": filter_value,
        "column_filter": column_filter
    })
@app.get("/daily-wireless", response_class=HTMLResponse)
def daily_wireless_page(
    request: Request,
    search_field: str = Query("사번"),
    search_value: str = Query(None),
    db: Session = Depends(get_db)
):
    entry = db.query(ExcelData).filter(
        ExcelData.sheet_name == "일자별무선",
        ExcelData.data_type == "종합"
    ).order_by(ExcelData.id.desc()).first()

    if not entry:
        return HTMLResponse("<h3>❌ '일자별무선' 데이터가 없습니다.</h3>")

    try:
        df = pd.read_json(BytesIO(entry.data.encode("utf-8")))
    except Exception:
        return HTMLResponse("<h3>❌ 데이터 파싱 오류 발생</h3>")

    df.columns = [col.strftime("%-m/%-d") if isinstance(col, pd.Timestamp) else str(col).strip() for col in df.columns]

    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].apply(lambda x: str(x).strip() if pd.notnull(x) else x)

    fixed_order = ["사번", "이름", "지사", "센터", "접점코드", "접점명"]
    other_cols = [col for col in df.columns if col not in fixed_order]
    df = df[fixed_order + other_cols]

    if "접점코드" in df.columns:
        code_map = get_code_to_user_mapping(db)
        df["사번"] = df["사번"] if "사번" in df.columns else ""
        df["이름"] = df["이름"] if "이름" in df.columns else ""

        mapped = df["접점코드"].map(code_map)
        mapped = mapped.dropna()
        mapped_df = mapped.apply(pd.Series)

        for idx in mapped_df.index:
            if pd.isna(df.at[idx, "사번"]) or df.at[idx, "사번"] == "":
                df.at[idx, "사번"] = mapped_df.at[idx, "사번"]
            if pd.isna(df.at[idx, "이름"]) or df.at[idx, "이름"] == "":
                df.at[idx, "이름"] = mapped_df.at[idx, "이름"]

    table_html = ""

    if not search_value:
        df = df.head(0)
        table_html = df.to_html(classes="table sticky-header", index=False, escape=False)
    else:
        if search_field not in df.columns:
            return HTMLResponse(f"<h3>⚠ '{search_field}' 컬럼이 존재하지 않습니다.</h3>")

        df = df[df[search_field].astype(str).str.strip() == search_value]

        if "접점코드" in df.columns:
            df["접점코드"] = df["접점코드"].apply(lambda x: f'<a href="/report?code={x}" target="_blank">{x}</a>')

        if not df.empty:
            numeric_cols = df.columns.difference(fixed_order)
            sum_row = {}
            for col in df.columns:
                if col in numeric_cols:
                    try:
                        values = pd.to_numeric(df[col], errors='coerce')
                        total = values.sum(skipna=True)
                        sum_row[col] = str(int(total)) if pd.notnull(total) and total != 0 else ""
                    except:
                        sum_row[col] = ""
                else:
                    sum_row[col] = "합계" if col == "사번" else ""

            sum_df = pd.DataFrame([sum_row])
            df = pd.concat([sum_df, df], ignore_index=True)

            # ✅ 정렬
            if "합계" in df.columns:
                try:
                    df = df.iloc[1:]
                    df["합계"] = pd.to_numeric(df["합계"], errors='coerce')
                    df = df.sort_values(by="합계", ascending=False)
                    df = pd.concat([sum_df, df], ignore_index=True)
                except:
                    pass

            table_html = df.to_html(classes="table sticky-header", index=False, escape=False)

            # ✅ 합계행 class
            table_html = table_html.replace("<tr>", "<tr class=\"sum-row\">", 1)

            # ✅ <th> class 삽입
            table_html = table_html.replace('<th>사번</th>', '<th class="sabun-col hidden-col">사번</th>')
            table_html = table_html.replace('<th>이름</th>', '<th class="name-col hidden-col">이름</th>')

            # ✅ <td> class 삽입 (합계 제외)
            if "사번" in df.columns:
                for val in df["사번"].astype(str).unique():
                    val = val.strip()
                    if val == "" or val == "합계":
                        continue
                    table_html = table_html.replace(f"<td>{val}</td>", f'<td class="sabun-col hidden-col">{val}</td>')

            if "이름" in df.columns:
                for val in df["이름"].astype(str).unique():
                    val = val.strip()
                    if val == "":
                        continue
                    table_html = table_html.replace(f"<td>{val}</td>", f'<td class="name-col hidden-col">{val}</td>')

        else:
            table_html = "<p style='color:red; font-weight:bold;'>❌ 검색 결과가 없습니다.</p>"

    return templates.TemplateResponse("daily-wireless.html", {
        "request": request,
        "search_field": search_field,
        "search_value": search_value,
        "table_html": table_html
    })

@app.get("/daily-wire", response_class=HTMLResponse)
async def daily_wire_page(
    request: Request,
    selected_sheet: str = Query(None),
    search_column: str = Query("사번"),
    search_value: str = Query(None),
    sort_column: str = Query("합계"),
    sort_order: str = Query("desc"),
    db: Session = Depends(get_db)
):
    sheet_map = {
        "신규+약갱": "신규+약갱개통",
        "신규개통 I+T": "신규개통I+T",
        "신규개통 I": "신규개통I",
        "신규개통 T": "신규개통T",
        "약갱개통 I+T": "약갱개통",
        "신규접수 I+T": "신규접수I+T",
        "신규접수 I": "신규접수I",
        "신규접수 T": "신규접수T"
    }

    table_html = "<p style='color:gray;'>시트를 선택해주세요</p>"

    if selected_sheet and selected_sheet in sheet_map:
        entry = db.query(ExcelData).filter(
            ExcelData.sheet_name == sheet_map[selected_sheet],
            ExcelData.data_type == "종합"
        ).order_by(ExcelData.id.desc()).first()

        if entry:
            try:
                df = pd.read_json(BytesIO(entry.data.encode("utf-8")))
                df.columns = [col.strip().replace(" ", "_") for col in df.columns]

                if "접점코드" in df.columns:
                    code_map = get_code_to_user_mapping(db)
                    df["사번"] = df.get("사번", "")
                    df["이름"] = df.get("이름", "")

                    mapped = df["접점코드"].map(code_map).dropna().apply(pd.Series)
                    for idx in mapped.index:
                        if not df.at[idx, "사번"]:
                            df.at[idx, "사번"] = mapped.at[idx, "사번"]
                        if not df.at[idx, "이름"]:
                            df.at[idx, "이름"] = mapped.at[idx, "이름"]

                fixed_order = ["사번", "이름", "지사", "센터", "접점코드", "접점명"]
                other_cols = [col for col in df.columns if col not in fixed_order]
                df = df[fixed_order + other_cols]

                if search_value:
                    if search_column in df.columns:
                        df = df[df[search_column].astype(str).str.strip() == search_value]

                if not df.empty:
                    numeric_cols = df.columns.difference(fixed_order)

                    if "접점코드" in df.columns:
                        df["접점코드"] = df["접점코드"].apply(lambda x: f'<a href="/report?code={x}" target="_blank">{x}</a>')

                    sum_row = {}
                    for col in df.columns:
                        if col in numeric_cols:
                            try:
                                values = pd.to_numeric(df[col], errors='coerce')
                                total = values.sum(skipna=True)
                                sum_row[col] = str(int(total)) if pd.notnull(total) and total != 0 else ""
                            except:
                                sum_row[col] = ""
                        else:
                            sum_row[col] = "합계" if col == "사번" else ""

                    sum_df = pd.DataFrame([sum_row])
                    df = pd.concat([sum_df, df], ignore_index=True)

                    # ✅ 정렬 적용
                    if sort_column in df.columns:
                        try:
                            df = df.iloc[1:]  # 합계 제외
                            df[sort_column] = pd.to_numeric(df[sort_column], errors='coerce')
                            df = df.sort_values(by=sort_column, ascending=(sort_order != "desc"))
                            df = pd.concat([sum_df, df], ignore_index=True)
                        except:
                            pass

                    # ✅ 테이블 HTML 생성
                    table_html = df.to_html(classes="table sticky-header", index=False, escape=False)
                    table_html = table_html.replace("<tr>", '<tr class="sum-row">', 1)

                    # ✅ <th> class 삽입
                    table_html = table_html.replace('<th>사번</th>', '<th class="sabun-col hidden-col">사번</th>')
                    table_html = table_html.replace('<th>이름</th>', '<th class="name-col hidden-col">이름</th>')

                    # ✅ <td> class 삽입 (합계 제외)
                    if "사번" in df.columns:
                        for val in df["사번"].dropna().astype(str).unique():
                            val = val.strip()
                            if val == "" or val == "합계":
                                continue
                            table_html = table_html.replace(
                                f"<td>{val}</td>",
                                f'<td class="sabun-col hidden-col">{val}</td>'
                            )

                    if "이름" in df.columns:
                        for val in df["이름"].dropna().astype(str).unique():
                            val = val.strip()
                            if val == "":
                                continue
                            table_html = table_html.replace(
                                f"<td>{val}</td>",
                                f'<td class="name-col hidden-col">{val}</td>'
                            )

                else:
                    table_html = "<p style='color:red; font-weight:bold;'>❌ 검색 결과가 없습니다.</p>"

            except Exception:
                table_html = "<h3>❌ 데이터 파싱 오류 발생</h3>"
        else:
            table_html = f"<h3>❌ '{sheet_map[selected_sheet]}' 데이터가 없습니다.</h3>"

    return templates.TemplateResponse("daily-wire.html", {
        "request": request,
        "selected_sheet": selected_sheet,
        "search_column": search_column,
        "search_value": search_value,
        "table_html": table_html,
        "sheet_options": list(sheet_map.keys())
    })


@app.get("/model-status", response_class=HTMLResponse)
async def model_status_page(
    request: Request,
    search_field: Optional[str] = Query(None),
    search_value: Optional[str] = Query(None),
    model_text: Optional[str] = Query(None),
    model_list: List[str] = Query(default_factory=list),
    exclude_branch: bool = Query(False),
    exclude_center: bool = Query(False),
    db: Session = Depends(get_db)
):
    data_entry = db.query(ExcelData).filter(
        ExcelData.sheet_name == "접점별 판매모델"
    ).order_by(ExcelData.id.desc()).first()

    if not data_entry:
        return HTMLResponse("<h3>❌ '접점별 판매모델' 시트 데이터가 없습니다.</h3>")

    df = pd.read_json(BytesIO(data_entry.data.encode("utf-8")))

    if "접점코드" in df.columns:
        code_map = get_code_to_user_mapping(db)
        df["사번"] = df.get("사번", "")
        df["이름"] = df.get("이름", "")
        mapped = df["접점코드"].map(code_map).dropna().apply(pd.Series)
        for idx in mapped.index:
            if not df.at[idx, "사번"]:
                df.at[idx, "사번"] = mapped.at[idx, "사번"]
            if not df.at[idx, "이름"]:
                df.at[idx, "이름"] = mapped.at[idx, "이름"]

    if model_text:
        df = df[df["모델"].astype(str).str.contains(model_text, case=False, na=False)]

    if model_list:
        df = df[df["모델"].isin(model_list)]

    if search_field and search_value:
        if search_field in df.columns:
            try:
                df = df[df[search_field].astype(str).str.strip() == search_value]
            except Exception:
                df = df.iloc[0:0]
        else:
            df = df.iloc[0:0]

    sum_columns = ["합계", "010", "MNP", "기변"]
    for col in sum_columns:
        if col not in df.columns:
            df[col] = 0
    df[sum_columns] = df[sum_columns].fillna(0)
    for col in sum_columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        
    df[sum_columns] = df[sum_columns].astype(int)

    group_cols = None
    if exclude_center:
        group_cols = ["지사", "모델"]
    elif exclude_branch:
        group_cols = ["지사", "센터", "모델"]
    if group_cols:
        df = df.groupby(group_cols, as_index=False)[sum_columns].sum(numeric_only=True)

    # ✅ 정렬 조건 처리
    if exclude_center:
        df = df.sort_values(by=["합계"], ascending=[False], ignore_index=True)
    elif exclude_branch:
        df = df.sort_values(by=["합계", "센터"], ascending=[False, False], ignore_index=True)
    else:
        df = df.sort_values(by=["합계", "접점코드", "센터"], ascending=[False, False, False], ignore_index=True)

    summary_row = {
        "지사": "합계",
        "센터": "",
        "모델": "",
        "접점코드": "",
        "접점명": "",
        "합계": int(df["합계"].sum()),
        "010": int(df["010"].sum()),
        "MNP": int(df["MNP"].sum()),
        "기변": int(df["기변"].sum()),
    }
    df = pd.concat([pd.DataFrame([summary_row]), df], ignore_index=True)

    cols_to_drop = ["사번", "이름"]
    if exclude_branch:
        cols_to_drop += ["접점코드", "접점명"]
    if exclude_center:
        cols_to_drop += ["센터"]
    df.drop(columns=[col for col in cols_to_drop if col in df.columns], inplace=True)

    if not exclude_branch and not exclude_center:
        desired_order = ["지사", "센터", "접점코드", "접점명", "모델", "합계", "010", "MNP", "기변"]
        df = df[[col for col in desired_order if col in df.columns]]

    if df.empty:
        table_html = "<p style='color:red; font-weight:bold;'>❌ 검색 결과가 없습니다. 검색어를 다시 확인해주세요.</p>"
    else:
        header_html = df.head(0).to_html(classes="table table-striped", index=False)
        body_html = df.to_html(index=False, header=False).split('<tbody>')[1].split('</tbody>')[0]
        table_html = f"""
            {header_html}
            <p id="loading-message" style="text-align:center;">⏳ 데이터 로딩 중...</p>
            <script>
                window.addEventListener("DOMContentLoaded", function() {{
                    const table = document.querySelector(".table");
                    const loading = document.getElementById("loading-message");
                    table.insertAdjacentHTML("beforeend", `{body_html}`);
                    const firstRow = table.querySelector("tbody tr:first-child");
                    if (firstRow) firstRow.classList.add("summary-row");
                    loading.remove();
                }});
            </script>
        """

    model_options = sorted(df["모델"].dropna().unique().tolist()) if "모델" in df.columns else []

    return templates.TemplateResponse("model-status.html", {
        "request": request,
        "search_field": search_field,
        "search_value": search_value,
        "model_text": model_text,
        "model_options": model_options,
        "selected_models": model_list,
        "table_html": table_html,
        "exclude_center": exclude_center,
    })



@app.get("/goal", response_class=HTMLResponse)
async def goal_page(request: Request):
    return templates.TemplateResponse("goal.html", {"request": request})



@app.get("/store", response_class=HTMLResponse)
async def store_page(
    request: Request,
    search_column: str = "사번",
    search_value: str = None,
    edit_mode: bool = False,
    db: Session = Depends(get_db)
):
    entry = db.query(StoreData).order_by(StoreData.id.desc()).first()

    if not entry and request.session.get("user_role") != "admin":
        return HTMLResponse("<h3>❌ 저장된 접점관리 데이터가 없습니다.</h3>")

    if not entry:
        df = pd.DataFrame()
        columns = []
        data = []
    else:
        df = pd.read_json(BytesIO(entry.data.encode("utf-8")))
        df.columns = pd.Index([str(col).strip().replace('\ufeff', '') for col in df.columns])
        df = df.apply(lambda col: col.map(lambda x: x if pd.isnull(x) else str(x).strip()))

        if "사번" not in df.columns:
            df["사번"] = ""
        if "이름" not in df.columns:
            df["이름"] = ""


        if search_value:
            search_value = search_value.strip()

        if search_value == "ALL":
            pass  # 전체 출력
        elif search_value and search_column in df.columns:
            df = df[df[search_column].astype(str).str.strip() == search_value]
        else:
            df = df.iloc[0:0]

        columns = df.columns.tolist()
        data = df.to_dict(orient="records")

    return templates.TemplateResponse("store.html", {
        "request": request,
        "session": request.session,
        "search_column": search_column,
        "search_value": search_value,
        "columns": columns,
        "data": data,
        "edit_mode": edit_mode
    })


router = APIRouter()

@router.post("/store/sync")
async def sync_store_data(entry: StoreEntry, db: Session = Depends(get_db)):
    code = entry.접점코드.strip()
    center = entry.센터.strip()

    # 1️⃣ Store 테이블 처리
    store = db.query(Store).filter(Store.접점코드 == code, Store.센터 == center).first()
    if store:
        # update
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

    # 2️⃣ StoreData 테이블의 JSON 처리
    entry_data = db.query(StoreData).order_by(StoreData.id.desc()).first()
    if not entry_data:
        return JSONResponse(content={"error": "StoreData 비어있음"}, status_code=400)

    df = pd.read_json(BytesIO(entry_data.data.encode("utf-8")))
    df.columns = df.columns.str.strip()

    # 동일한 접점코드 + 센터가 있으면 update, 아니면 append
    match = (df["접점코드"].astype(str).str.strip() == code) & (df["센터"].astype(str).str.strip() == center)
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


app.include_router(router)



@app.get("/store/export")
async def export_store_data(db: Session = Depends(get_db)):
    entry = db.query(StoreData).order_by(StoreData.id.desc()).first()
    if not entry:
        return HTMLResponse("❌ 저장된 접점관리 데이터가 없습니다.")

    df = pd.read_json(BytesIO(entry.data.encode("utf-8")))

    now = datetime.now()
    filename_raw = f"전사접점코드_{now.strftime('%y%m%d_%H%M')}.xlsx"
    filename_encoded = quote(filename_raw)  # 한글 포함 시 URL 인코딩

    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="접점관리")
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            # RFC 6266 표준 준수 (filename*=... 형식)
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}"
        }
    )


@app.post("/store/update")
async def update_store_data(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    entry = db.query(StoreData).order_by(StoreData.id.desc()).first()

    if not entry:
        return HTMLResponse("<h3>❌ 저장된 데이터가 없습니다.</h3>")

    df = pd.read_json(BytesIO(entry.data.encode("utf-8")))
    df.columns = [col.strip() for col in df.columns]
    df = df.applymap(lambda x: str(x).strip() if pd.notnull(x) else x)  # 문자열 정리

    temp_rows = {}
    for key, value in form_data.items():
        if "_" in key and not key.startswith("new_"):
            code, col = key.split("_", 1)
            temp_rows.setdefault(code.strip(), {})[col.strip()] = value.strip()

    for code, row_data in temp_rows.items():
        try:
            row_idx = df[df["접점코드"].str.strip().str.upper() == code.strip().upper()].index[0]
            for col in row_data:
                if col in df.columns:
                    df.at[row_idx, col] = row_data[col]
        except Exception as e:
            print(f"❌ 수정할 행을 찾을 수 없습니다: {code} -> {e}")

    entry.data = df.to_json(force_ascii=False, orient="records")
    db.commit()

    return HTMLResponse("<script>alert('✅ 수정사항이 저장되었습니다.'); location.href='/store';</script>")


@app.post("/store/from-report")
async def create_store_from_report(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()

    new_row = {
        "접점코드": form_data.get("접점코드", "").strip(),
        "접점명": form_data.get("접점명", "").strip(),
        "지사": form_data.get("지사", "").strip(),
        "센터": form_data.get("센터", "").strip(),
        "사번": form_data.get("사번", "").strip(),
        "이름": form_data.get("이름", "").strip(),
        "주소": form_data.get("주소", "").strip(),
    }

    # 기존 StoreData에서 최신 데이터 불러오기
    entry = db.query(StoreData).order_by(StoreData.id.desc()).first()
    if not entry:
        return HTMLResponse("<script>alert('❌ 저장 실패: StoreData가 없습니다. 먼저 초기 업로드를 해주세요.'); history.back();</script>")

    df = pd.read_json(BytesIO(entry.data.encode("utf-8")))
    df.columns = df.columns.str.strip()

    # 같은 접점코드 존재 시 업데이트
    code = new_row["접점코드"]
    if code in df["접점코드"].values:
        df.loc[df["접점코드"] == code] = [new_row.get(col, "") for col in df.columns]
    else:
        df.loc[len(df)] = [new_row.get(col, "") for col in df.columns]

    entry.data = df.to_json(force_ascii=False, orient="records")
    db.commit()

    return HTMLResponse("<script>alert('✅ 접점 정보가 저장되었습니다.'); location.href='/report?code=" + code + "&center=" + new_row["센터"] + "';</script>")



@app.post("/store/create")
async def create_store_data(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()

    # ✅ 명확한 컬럼명 추출 방식 사용
    new_row = {}
    for key, value in form_data.items():
        if key.startswith("new_"):
            col = key[4:]
            new_row[col] = value

    entry = db.query(StoreData).order_by(StoreData.id.desc()).first()
    if not entry:
        return HTMLResponse("<h3>❌ 저장된 데이터가 없습니다. 먼저 초기 데이터를 업로드하세요.</h3>")

    df = pd.read_json(BytesIO(entry.data.encode("utf-8")))
    df.columns = [col.strip() for col in df.columns]

    new_df = pd.DataFrame([[new_row.get(col, "") for col in df.columns]], columns=df.columns)
    df = pd.concat([new_df, df], ignore_index=True)

    entry.data = df.to_json(force_ascii=False, orient="records")
    db.commit()

    return HTMLResponse("<script>alert('✅ 신규 거래처가 추가되었습니다.'); location.href='/store';</script>")


@app.post("/init-store")
async def upload_init_store(file: UploadFile = File(...), db: Session = Depends(get_db)):
    df = pd.read_excel(file.file, sheet_name="접점관리")
    df = df.fillna("")

    # ✅ 문자열로 바꾸고 strip 처리
    df = df.apply(lambda col: col.map(lambda x: str(x).strip() if pd.notnull(x) else ""))

    store_data = StoreData(data=df.to_json(force_ascii=False, orient="records"))
    db.add(store_data)
    db.commit()

    return HTMLResponse("<script>alert('✅ 접점관리 데이터 초기 저장 완료!'); location.href='/store';</script>")


@app.post("/store/delete")
async def delete_store(code: str = Form(...), db: Session = Depends(get_db)):
    entry = db.query(StoreData).order_by(StoreData.id.desc()).first()
    if not entry:
        return HTMLResponse("❌ 저장된 접점관리 데이터가 없습니다.")

    df = pd.read_json(BytesIO(entry.data.encode("utf-8")))
    df.columns = [col.strip() for col in df.columns]

    # 대소문자 무관 비교 후 제거
    df = df[~df["접점코드"].str.lower().eq(code.lower())]

    entry.data = df.to_json(force_ascii=False, orient="records")
    db.commit()

    return RedirectResponse("/store", status_code=303)



@app.post("/store/region-upload")
async def upload_region_store(region: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)):
    # ✅ form에서 받은 값 정리 (strip + lower)
    region = region.strip().lower()

    # ✅ 엑셀 파일 안전하게 읽기 (FastAPI UploadFile 스트림 문제 대응)
    content = await file.read()
    try:
        df_new = pd.read_excel(BytesIO(content), sheet_name=region)
    except ValueError:
            return HTMLResponse(f"<script>alert('❌ 엑셀에 \"{region}\" 시트가 없습니다.'); history.back();</script>")

    df_new.columns = [col.strip().replace("\ufeff", "") for col in df_new.columns]  # BOM 제거 등

    # ✅ 지사 컬럼 존재 확인
    if "지사" not in df_new.columns:
        return HTMLResponse("<script>alert('❌ 업로드 파일에 지사 컬럼이 없습니다.'); history.back();</script>")

    # ✅ 지사 컬럼 정규화 (strip + lower)
    df_new["지사"] = df_new["지사"].astype(str).fillna("").str.strip().str.lower()

    # ✅ 업로드된 엑셀 중 선택된 지사 데이터만 필터
    df_new_filtered = df_new[df_new["지사"] == region]

    # ✅ 디버깅용 로그
    print("📌 선택한 지사(region):", repr(region))
    print("📌 엑셀 내 지사들:", df_new["지사"].unique().tolist())
    print("📌 필터된 행 수:", len(df_new_filtered))

    if df_new_filtered.empty:
        return HTMLResponse(f"<script>alert('❌ 엑셀에 \"{region}\" 지사 데이터가 없습니다.'); history.back();</script>")

    # ✅ 기존 DB 데이터 불러오기
    entry = db.query(StoreData).order_by(StoreData.id.desc()).first()
    if not entry:
        return HTMLResponse("<script>alert('❌ 기존 데이터가 없습니다.'); location.href='/store';</script>")

    # ✅ 기존 데이터에서 선택된 지사 제외
    df = pd.read_json(BytesIO(entry.data.encode("utf-8")))
    df["지사"] = df["지사"].fillna("").astype(str).str.strip().str.lower()
    df_other = df[df["지사"] != region]

    # ✅ 새 지사 데이터로 덮어쓰기
    df_combined = pd.concat([df_other, df_new_filtered], ignore_index=True)
    entry.data = df_combined.to_json(force_ascii=False, orient="records")
    db.commit()

    return HTMLResponse("<script>alert('✅ 지사별 데이터 업로드 완료'); location.href='/store';</script>")



@app.get("/store/region-export")
async def export_region_store(region: str, db: Session = Depends(get_db)):
    entry = db.query(StoreData).order_by(StoreData.id.desc()).first()
    if not entry:
        return HTMLResponse("❌ 저장된 데이터가 없습니다.")

    region = region.strip()
    df = pd.read_json(BytesIO(entry.data.encode("utf-8")))
    df["지사"] = df["지사"].fillna("").astype(str).str.strip()

    df_region = df[df["지사"] == region]
    if df_region.empty:
        return HTMLResponse(f"<script>alert('❌ \"{region}\" 지사 데이터가 없습니다.'); history.back();</script>")

    # 파일명 생성: 지사_250613_0914.xlsx
    now = datetime.now()
    filename_raw = f"{region}_{now.strftime('%y%m%d_%H%M')}.xlsx"
    filename_encoded = quote(filename_raw)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df_region.to_excel(writer, index=False, sheet_name=region)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}"
        }
    )

@app.post("/store/center-upload")
async def upload_center_store(center_name: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)):
    # ✅ 사용자 입력값 정제
    center_name = center_name.strip().lower()

    # ✅ 파일 안전하게 읽기
    content = await file.read()
    try:
        df_new = pd.read_excel(BytesIO(content), sheet_name=center_name)
    except ValueError as e:
        return HTMLResponse(f"<script>alert('❌ 엑셀에 \"{center_name}\" 시트가 없습니다.'); history.back();</script>")

    df_new.columns = [col.strip().replace("\ufeff", "") for col in df_new.columns]

    # ✅ 센터 컬럼 확인
    if "센터" not in df_new.columns:
        return HTMLResponse("<script>alert('❌ 업로드 파일에 센터 컬럼이 없습니다.'); history.back();</script>")

    # ✅ 센터 컬럼 정규화
    df_new["센터"] = df_new["센터"].astype(str).fillna("").str.strip().str.lower()

    # ✅ 업로드된 엑셀 중 해당 센터만 필터
    df_new_filtered = df_new[df_new["센터"] == center_name]

    # ✅ 디버깅 로그
    print("📌 선택한 센터(center_name):", repr(center_name))
    print("📌 엑셀 내 센터들:", df_new["센터"].unique().tolist())
    print("📌 필터된 행 수:", len(df_new_filtered))

    if df_new_filtered.empty:
        return HTMLResponse(f"<script>alert('❌ 엑셀에 \"{center_name}\" 센터 데이터가 없습니다.'); history.back();</script>")

    # ✅ 기존 DB 데이터 로드 및 센터 정규화
    entry = db.query(StoreData).order_by(StoreData.id.desc()).first()
    if not entry:
        return HTMLResponse("<script>alert('❌ 기존 데이터가 없습니다.'); location.href='/store';</script>")

    df = pd.read_json(BytesIO(entry.data.encode("utf-8")))
    df["센터"] = df["센터"].fillna("").astype(str).str.strip().str.lower()

    # ✅ 해당 센터 제외한 데이터 유지 후 병합
    df_other = df[df["센터"] != center_name]
    df_combined = pd.concat([df_other, df_new_filtered], ignore_index=True)

    # ✅ 저장
    entry.data = df_combined.to_json(force_ascii=False, orient="records")
    db.commit()

    return HTMLResponse("<script>alert('✅ 센터별 데이터 업로드 완료'); location.href='/store';</script>")


@app.get("/store/center-export")
async def export_center_store(center_name: str, db: Session = Depends(get_db)):
    entry = db.query(StoreData).order_by(StoreData.id.desc()).first()
    if not entry:
        return HTMLResponse("❌ 저장된 데이터가 없습니다.")

    center_name = center_name.strip()
    df = pd.read_json(BytesIO(entry.data.encode("utf-8")))
    df["센터"] = df["센터"].fillna("").astype(str).str.strip()
    df_center = df[df["센터"] == center_name]

    if df_center.empty:
        return HTMLResponse(f"<script>alert('❌ \"{center_name}\" 센터 데이터가 없습니다.'); history.back();</script>")

    # 파일명 생성
    now = datetime.now()
    filename_raw = f"{center_name}_{now.strftime('%y%m%d_%H%M')}.xlsx"
    filename_encoded = quote(filename_raw)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df_center.to_excel(writer, index=False, sheet_name=center_name)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}"
        }
    )

@app.get("/infra", response_class=HTMLResponse)
async def infra_page(
    request: Request,
    selected_sheets: List[str] = Query(default_factory=list),
    filter_column: str = Query("지사"),
    filter_value: str = Query(""),
    db: Session = Depends(get_db)
):
    SHEET_LABELS = {
        "전월 무선가동점": "전월가동(무선)",
        "전월 유선가동점": "전월가동(유선)",
        "2개월 무선 연속가동점": "2개월(무선)",
        "2개월 유선 연속가동점": "2개월(유선)",
        "전월 신규점": "전월신규점"
    }

    code_map = get_code_to_user_mapping(db) or {}
    tables = {}
    summary_data = []

    if not selected_sheets and not filter_value:
        selected_sheets.append("전월 무선가동점")

    for label, sheet_name in SHEET_LABELS.items():
        data_entry = db.query(ExcelData).filter(
            ExcelData.sheet_name == sheet_name
        ).order_by(ExcelData.id.desc()).first()

        if not data_entry:
            summary_data.append({
                "label": label,
                "total_points": 0,
                "active_points": 0,
                "inactive_points": 0,
                "active_rate": "0%"
            })
            continue

        df = pd.read_json(BytesIO(data_entry.data.encode("utf-8")))
        df.columns = [col.strip().replace(" ", "_") for col in df.columns]

        if "접점코드" in df.columns:
            df["사번"] = df.get("사번", "")
            df["이름"] = df.get("이름", "")
            mapped = df["접점코드"].map(code_map).dropna().apply(pd.Series)
            for idx in mapped.index:
                if not df.at[idx, "사번"]:
                    df.at[idx, "사번"] = mapped.at[idx, "사번"]
                if not df.at[idx, "이름"]:
                    df.at[idx, "이름"] = mapped.at[idx, "이름"]

        filtered_df = df.copy()
        if filter_column and filter_value:
            if filter_column in filtered_df.columns:
                filtered_df = filtered_df[
                    filtered_df[filter_column].astype(str).str.strip() == filter_value
                ]

        sort_columns = [col for col in ["전월무선", "전월유선"] if col in filtered_df.columns]
        if sort_columns:
            try:
                for col in sort_columns:
                    filtered_df[col] = pd.to_numeric(filtered_df[col], errors="coerce")
                filtered_df = filtered_df.sort_values(by=sort_columns, ascending=False)
            except:
                pass

        total_points = len(filtered_df)
        active_points = filtered_df[filtered_df["가동여부"].str.upper() == "O"].shape[0] if "가동여부" in filtered_df.columns else 0
        inactive_points = total_points - active_points
        active_rate = f"{round((active_points / total_points) * 100, 1)}%" if total_points > 0 else "0%"

        summary_data.append({
            "label": label,
            "total_points": total_points,
            "active_points": active_points,
            "inactive_points": inactive_points,
            "active_rate": active_rate
        })

        if label in selected_sheets:
            html = filtered_df.to_html(classes="table table-striped", index=False, escape=False)

            # ✅ <th>에 class만 삽입 (숨김 X)
            html = html.replace('<th>사번</th>', '<th class="sabun-col">사번</th>')
            html = html.replace('<th>이름</th>', '<th class="name-col">이름</th>')

            # ✅ <td>에 class만 삽입 (숨김 X, "합계" 제외)
            if "사번" in filtered_df.columns:
                for val in filtered_df["사번"].dropna().astype(str).unique():
                    val = val.strip()
                    if val == "" or val == "합계":
                        continue
                    html = html.replace(
                        f"<td>{val}</td>",
                        f'<td class="sabun-col">{val}</td>'
                    )

            if "이름" in filtered_df.columns:
                for val in filtered_df["이름"].dropna().astype(str).unique():
                    val = val.strip()
                    if val == "":
                        continue
                    html = html.replace(
                        f"<td>{val}</td>",
                        f'<td class="name-col">{val}</td>'
                    )

            tables[label] = html

    return templates.TemplateResponse("infra.html", {
        "request": request,
        "sheet_options": list(SHEET_LABELS.keys()),
        "selected_sheets": selected_sheets,
        "filter_column": filter_column,
        "filter_value": filter_value,
        "tables": tables,
        "summary_data": summary_data
    })

    # main.py (타이틀 수정 라우터)

@app.get("/admin/update-title", response_class=HTMLResponse)
async def update_title_page(request: Request, db: Session = Depends(get_db)):
    if request.session.get("user_role") != "admin":
        return HTMLResponse("<h3>⚠ 관리자 전용 페이지입니다.</h3>", status_code=403)

    setting = db.query(SiteSettings).first()
    return templates.TemplateResponse("update_title.html", {
        "request": request,
        "current_title": setting.title if setting else "",
        "current_notice": setting.notice if setting else "",
        "current_issue": setting.issue if setting else ""
    })

@app.post("/admin/update-title")
async def update_title(
    request: Request,
    new_notice: str = Form(...),
    new_issue: str = Form(...),
    db: Session = Depends(get_db)
):
    if request.session.get("user_role") != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")

    setting = db.query(SiteSettings).first()
    if setting:
        setting.notice = new_notice.strip()
        setting.issue = new_issue.strip()
    else:
        setting = SiteSettings(
            notice=new_notice.strip(),
            issue=new_issue.strip()
        )
        db.add(setting)

    db.commit()
    db.refresh(setting)
    print(f"✅ 업데이트된 정보: {setting.notice}, {setting.issue}")

    return HTMLResponse("<script>alert('✅ 공지사항/이슈사항이 업데이트되었습니다!'); location.href='/main';</script>")

