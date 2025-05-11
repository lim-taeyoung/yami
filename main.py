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

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from sqlalchemy import create_engine, Column, Integer, String, Boolean, ForeignKey, Text
from sqlalchemy.orm import sessionmaker, Session
from database import Base
from models import ExcelData, BoardReply, Store, SiteSettings 
from database import get_db, engine 

from starlette.middleware.sessions import SessionMiddleware

from settings import DATABASE_URL

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.add_middleware(SessionMiddleware, secret_key="supersecret123!@#")
app.mount("/static", StaticFiles(directory="static"), name="static")
UPLOAD_PATH = "static/uploads"
MAIN_IMAGE_FILENAME = "main_banner.jpg"

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


class SiteSettings(Base):
    __tablename__ = "site_settings"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)


    
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

    # ✅ 세션에 관리자 여부 저장
    request.session["username"] = user.username
    request.session["user_role"] = "admin" if user.role == "관리자" else "user"
    request.session["name"] = user.name  # ✅ 이름도 저장!

    return RedirectResponse(url=f"/main?username={user.username}", status_code=303)

@app.get("/login-admin")
async def login_as_admin(request: Request):
    request.session["user_role"] = "admin"
    return RedirectResponse("/store", status_code=302)


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


# ✅ 메인 페이지 (로그인 후 이동)
@app.get("/main", response_class=HTMLResponse)
def main_page(request: Request, username: str = Query("사용자"), mode: str = Query("mobile"), db: Session = Depends(get_db)):
    name = request.session.get("name", "사용자")

    # ✅ 대문 이미지가 존재하면 경로 전달
    image_path = "static/uploads/main_banner.jpg"
    image_url = f"/{image_path}" if os.path.exists(image_path) else None

    # ✅ 타이틀 읽기 (DB에서 최신 타이틀 읽기)
    title = db.query(SiteSettings).first()
    title_text = title.title if title else "업데이트된 타이틀이 없습니다."

    print(f"✅ 타이틀 텍스트: {title_text}")  # ✅ 디버깅: 타이틀 출력 확인

    return templates.TemplateResponse("main.html", {
        "request": request,
        "username": username,
        "mode": mode,
        "name": name,
        "main_image_url": image_url,  # ✅ 이미지 경로 넘겨줌
        "title_text": title_text      # ✅ 타이틀 텍스트 넘겨줌
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

    df = pd.read_json(BytesIO(latest_data.data.encode("utf-8")))
    df.columns = df.columns.str.strip()

    # ✅ 사용자 매핑 먼저!
    df = apply_user_mapping(df, db)

    # ✅ 컬럼 검사 여기서!
    if search_column not in df.columns:
        print("❌ 컬럼 없음 오류 발생! 현재 df.columns:", df.columns.tolist())
        return HTMLResponse(content=f"<p>⚠ '{search_column}' 컬럼이 데이터에 없습니다.</p>")

    base_columns = [col for col in ["사번", "이름", "지사", "센터", "접점코드", "접점명"] if col in df.columns]

    mapped_columns = {
        "일반후불(M,대)": ["M-3 무선(M)", "M-2 무선(M)", "M-1 무선(M)", "M-3 무선(대)", "M-2 무선(대)", "M-1 무선(대)"],
        "유선신규(M,대)": ["M-3 유선신규(M)", "M-2 유선신규(M)", "M-1 유선신규(M)", "M-3 유선신규(대)", "M-2 유선신규(대)", "M-1 유선신규(대)"]
    }

    expanded_columns = []
    for col in columns:
        expanded_columns.extend(mapped_columns.get(col, [col]))

    selected_columns = base_columns + [col for col in expanded_columns if col in df.columns and col not in base_columns]
    df = df[selected_columns]

    if not search_value:
        table_html = ""
    else:
        try:
            df = df[df[search_column].astype(str).str.contains(search_value, case=False, na=False)]
        except Exception as e:
            return HTMLResponse(content=f"<p style='color:red;'>🔥 필터링 중 오류 발생: {e}</p>")

        if df.empty:
            return HTMLResponse(content="<p style='color:red; font-weight:bold; font-size:50px;'>❌ 검색 결과가 없습니다.</p>")

        # 일반후불 정렬
        sort_column = "일반후불"
        if sort_column in df.columns:
            try:
                df[sort_column] = pd.to_numeric(df[sort_column], errors='coerce')
                df = df.sort_values(by=sort_column, ascending=False)
            except Exception as e:
                print(f"⚠ 정렬 오류: {e}")

        # 요약행 생성
        sum_cols = [
            "일반후불", "010", "MNP", "기변", "중고", "5G", "3G/LTE", "100K이상", "초이스4종",
            "유선신규 I+T", "유선신규 I", "유선신규 T", "유선약갱 I+T", "유선약갱 I", "유선약갱 T",
            "MIT(M) 합계", "MIT(M) 신규", "MIT(M) 약갱", "MIT(I) 합계", "MIT(I) 신규", "MIT(I) 약갱",
            "신동", "S25", "AIP16",
            "M-3 무선(M)", "M-2 무선(M)", "M-1 무선(M)", "M-3 무선(대)", "M-2 무선(대)", "M-1 무선(대)",
            "M-3 유선신규(M)", "M-2 유선신규(M)", "M-1 유선신규(M)", "M-3 유선신규(대)", "M-2 유선신규(대)", "M-1 유선신규(대)"
        ]
        summary = {}
        for col in df.columns:
            if col in base_columns:
                summary[col] = ""
            elif col in sum_cols:
                try:
                    value = pd.to_numeric(df[col], errors='coerce').sum(skipna=True)
                    summary[col] = int(value) if pd.notnull(value) else 0
                except:
                    summary[col] = 0
            else:
                summary[col] = ""

        if "신동률" in df.columns:
            try:
                신동 = float(summary.get("신동", 0) or 0)
                mnp = float(summary.get("MNP", 0) or 0)
                summary["신동률"] = f"{round((신동 / mnp) * 100, 1)}%" if mnp > 0 else "--"
            except:
                summary["신동률"] = "--"

            def format_rate(val):
                try:
                    val = str(val).strip().replace('%', '')
                    return f"{round(float(val), 1)}%"
                except (ValueError, TypeError):
                    return val

            df["신동률"] = df["신동률"].apply(format_rate)

        df = pd.concat([pd.DataFrame([summary]), df], ignore_index=True)

        if "접점코드" in df.columns:
            df["접점코드"] = df["접점코드"].apply(
                lambda x: f'<a href="/report?code={x}" target="_blank">{x}</a>' if pd.notnull(x) else ""
            )

        df_visible = df[[col for col in df.columns if col not in ["사번", "이름"]]]
        table_html = df_visible.to_html(classes="table table-striped", index=False, escape=False)

        table_html = table_html.replace('<th>지사</th>', '<th class="sticky-col col-1">지사</th>')
        table_html = table_html.replace('<th>센터</th>', '<th class="sticky-col col-2">센터</th>')
        table_html = table_html.replace('<th>접점코드</th>', '<th class="sticky-col col-3">접점코드</th>')
        table_html = table_html.replace('<th>접점명</th>', '<th class="sticky-col col-4">접점명</th>')

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "type": type,
        "search_column": search_column,
        "search_value": search_value,
        "columns": columns,
        "table_html": table_html,
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

    return templates.TemplateResponse("board.html", {
        "request": request,
        "messages": messages,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count
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

@app.get("/report", response_class=HTMLResponse)
async def render_report(request: Request, code: str = Query(...), db: Session = Depends(get_db)):
    # ✅ 종합현황 데이터
    data_entry = db.query(ExcelData).filter(
        ExcelData.sheet_name == "종합현황"
    ).order_by(ExcelData.id.desc()).first()

    if not data_entry:
        return HTMLResponse("<h3>❌ 종합현황 데이터가 없습니다.</h3>")

    df = pd.read_json(BytesIO(data_entry.data.encode("utf-8")))
    df["접점코드"] = df["접점코드"].astype(str).str.strip().str.upper()

    if code not in df["접점코드"].values:
        return HTMLResponse("<h3>❌ 해당 접점코드가 존재하지 않습니다.</h3>")

    row = df[df["접점코드"] == code].iloc[0]

    def get(col):
        return row[col] if col in row else "-"

    # ✅ 사번/이름 매핑 적용 (StoreData 기준)
    code_map = get_code_to_user_mapping(db)
    user_info = code_map.get(code.upper(), {"사번": "-", "이름": "-"})

    # ✅ 접점별 판매모델
    model_entry = db.query(ExcelData).filter(
        ExcelData.sheet_name == "접점별 판매모델"
    ).order_by(ExcelData.id.desc()).first()

    model_data = []
    if model_entry:
        model_df = pd.read_json(BytesIO(model_entry.data.encode("utf-8")))
        model_df = model_df[model_df["접점코드"].astype(str).str.upper() == code.upper()]
        if not model_df.empty:
            model_df = model_df[["모델", "합계", "010", "MNP", "기변"]].fillna(0)
            model_df = model_df.groupby("모델", as_index=False).sum(numeric_only=True)
            model_df = model_df.sort_values(by="합계", ascending=False).head(8)
            model_data = model_df.to_dict(orient="records")

    return templates.TemplateResponse("report.html", {
        "request": request,
        "get": get,
        "model_data": model_data,
        "user_info": user_info  # 👉 사번/이름 추가로 넘김
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
    all_columns = df.columns.tolist()

    # ✅ 접점코드 기준으로 사번/이름 자동 매핑
    if "접점코드" in df.columns:
        code_map = get_code_to_user_mapping(db)

        if "사번" not in df.columns:
            df["사번"] = ""
        if "이름" not in df.columns:
            df["이름"] = ""

        mapped = df["접점코드"].map(code_map).dropna()
        mapped_df = mapped.apply(pd.Series)

        for idx in mapped_df.index:
            if pd.isna(df.at[idx, "사번"]) or df.at[idx, "사번"] == "":
                df.at[idx, "사번"] = mapped_df.at[idx, "사번"]
            if pd.isna(df.at[idx, "이름"]) or df.at[idx, "이름"] == "":
                df.at[idx, "이름"] = mapped_df.at[idx, "이름"]

    show_data = bool(filter_value)

    if show_data and filter_column and filter_value and filter_column in df.columns:
        df = df[df[filter_column].astype(str).str.contains(filter_value)]

    def convert_percent(x):
        if pd.isnull(x):
            return "--"
        try:
            x_str = str(x).strip()
            if "%" in x_str:
                # 퍼센트 기호가 있는 경우, 그대로 숫자만 추출해 사용
                val = float(x_str.replace('%', ''))
            else:
                # 비율(0.85 등)로 들어온 경우 → 퍼센트로 환산
                val = float(x_str) * 100
            return f"{round(val, 1)}%"
        except:
            return str(x)

    fixed_columns = ["사번", "이름", "지사", "센터", "접점코드", "접점명"]

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
        df = df[[col for col in selected_cols if col in df.columns]]
    else:
        df = df[[col for col in fixed_columns if col in df.columns]]

    if "접점코드" in df.columns:
        df["접점코드"] = df["접점코드"].apply(lambda x: f'<a href="/report?code={x}" target="_blank">{x}</a>')

    table_html = ""

    if show_data:
        sum_columns = ["목표", "무선", "MIT", "MIT(실적인정)", "MNP", "신동(개통)", "지원금"]
        base_columns = ["사번", "이름", "지사", "센터", "접점코드", "접점명", "계약시작", "계약종료", "잔여계약일"]

        summary = {}
        for col in df.columns:
            if col in base_columns:
                summary[col] = ""
            elif col in sum_columns:
                try:
                    numeric_col = pd.to_numeric(df[col], errors='coerce')
                    value = numeric_col.sum(skipna=True)
                    summary[col] = value if pd.notnull(value) else 0.0
                except:
                    summary[col] = 0.0
            else:
                summary[col] = ""

        if "달성률" in df.columns:
            try:
                무선 = float(summary.get("무선", 0) or 0)
                mit = float(summary.get("MIT(실적인정)", 0) or 0)
                목표 = float(summary.get("목표", 0) or 0)
                if 목표 > 0:
                    summary["달성률"] = f"{round((무선 + mit) / 목표 * 100, 1)}%"
                else:
                    summary["달성률"] = "--"
            except:
                summary["달성률"] = "--"

            df["달성률"] = df["달성률"].apply(convert_percent)

        if "신동률(개통)" in df.columns:
            try:
                신동 = float(summary.get("신동(개통)", 0) or 0)
                mnp = float(summary.get("MNP", 0) or 0)
                if mnp > 0:
                    summary["신동률(개통)"] = f"{round(신동 / mnp * 100, 1)}%"
                else:
                    summary["신동률(개통)"] = "--"
            except:
                summary["신동률(개통)"] = "--"

            df["신동률(개통)"] = df["신동률(개통)"].apply(convert_percent)

        # ✅ 합계 행 삽입
        summary_row = pd.DataFrame([summary])
        summary_row.index = ["합계"]
        df = pd.concat([summary_row, df], ignore_index=False)

        # ✅ HTML로 렌더링
        table_html = df.to_html(classes="table table-striped", index=False, escape=False)

        # ✅ 합계 행에 .sum-row 클래스 강제 삽입
        table_html = table_html.replace("<tr>", '<tr class="sum-row">', 1)

    else:
        table_html = df.head(0).to_html(classes="table table-striped", index=False, escape=False)

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

        df = df[df[search_field].astype(str).str.contains(search_value, case=False, regex=False)]

        if "접점코드" in df.columns:
            df["접점코드"] = df["접점코드"].apply(lambda x: f'<a href="/report?code={x}" target="_blank">{x}</a>')

                        # ✅ 합계 생성
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
                    sum_row[col] = ""

            sum_df = pd.DataFrame([sum_row])
            df = pd.concat([sum_df, df], ignore_index=True)

            table_html = df.to_html(classes="table sticky-header", index=False, escape=False)
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

                # ✅ 접점코드 매핑
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

                # ✅ 컬럼 순서
                fixed_order = ["사번", "이름", "지사", "센터", "접점코드", "접점명"]
                other_cols = [col for col in df.columns if col not in fixed_order]
                df = df[fixed_order + other_cols]

                # ✅ 검색
                if search_value:
                    if search_column in df.columns:
                        df = df[df[search_column].astype(str).str.contains(search_value, na=False)]

                # ✅ 합계 생성
                if not df.empty:
                    numeric_cols = df.columns.difference(fixed_order)

                  # 접점코드 링크 처리
                    if "접점코드" in df.columns:
                        df["접점코드"] = df["접점코드"].apply(lambda x: f'<a href="/report?code={x}" target="_blank">{x}</a>')

                # 합계 생성
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
                            sum_row[col] = ""

                    sum_df = pd.DataFrame([sum_row])
                    df = pd.concat([sum_df, df], ignore_index=True)

                    table_html = df.to_html(classes="table sticky-header", index=False, escape=False)
                    table_html = table_html.replace("<tr>", "<tr class=\"sum-row\">", 1)
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
    db: Session = Depends(get_db)
):
    data_entry = db.query(ExcelData).filter(
        ExcelData.sheet_name == "접점별 판매모델"
    ).order_by(ExcelData.id.desc()).first()

    if not data_entry:
        return HTMLResponse("<h3>❌ '접점별 판매모델' 시트 데이터가 없습니다.</h3>")

    df = pd.read_json(BytesIO(data_entry.data.encode("utf-8")))

    # 접점코드 → 사번/이름 매핑
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

    # 모델 필터
    if model_text:
        df = df[df["모델"].astype(str).str.contains(model_text, case=False, na=False)]

    if model_list:
        df = df[df["모델"].isin(model_list)]

    if search_field and search_value:
        if search_field in df.columns:
            try:
                df = df[df[search_field].astype(str).str.contains(search_value, case=False, regex=False)]
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

    if exclude_branch:
        # ✅ 접점제외 시 모델 중복 제거 + 실적 합산
        group_cols = ["지사", "센터", "모델"]
        df = df.groupby(group_cols, as_index=False)[sum_columns].sum(numeric_only=True)

    # ✅ 정렬: 조건에 따라 분기 처리
    df["센터"] = df["센터"].fillna("").astype(str)
    if search_field == "지사":
        df = df.sort_values(by=["센터", "합계"], ascending=[False, False], ignore_index=True)
    else:
        df = df.sort_values(by=["합계", "센터"], ascending=[False, False], ignore_index=True)

    # ✅ 합계 행 삽입
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

    # ✅ 컬럼 제거
    cols_to_drop = ["사번", "이름"]
    if exclude_branch:
        cols_to_drop += ["접점코드", "접점명"]
    df.drop(columns=[col for col in cols_to_drop if col in df.columns], inplace=True)

    # ✅ 검색하기 버튼일 경우 → 컬럼 순서 지정
    if not exclude_branch:
        desired_order = ["지사", "센터", "접점코드", "접점명", "모델", "합계", "010", "MNP", "기변"]
        df = df[[col for col in desired_order if col in df.columns]]

    # ✅ 테이블 생성
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
        "table_html": table_html
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

    # ✅ entry가 없고 관리자가 아니면 메시지 출력
    if not entry and request.session.get("user_role") != "admin":
        return HTMLResponse("<h3>❌ 저장된 접점관리 데이터가 없습니다.</h3>")

    # ✅ entry가 없지만 admin이면 빈 테이블 보여주기
    if not entry:
        df = pd.DataFrame()
        columns = []
        data = []
    else:
        df = pd.read_json(BytesIO(entry.data.encode("utf-8")))

        # ✅ 컬럼명 정리: 공백 제거 + BOM 제거
        df.columns = pd.Index([
            str(col).strip().replace('\ufeff', '') if isinstance(col, str) else col
            for col in df.columns
        ])

        # ✅ 값 정리: 문자열 공백 제거
        df = df.apply(lambda col: col.map(lambda x: x if pd.isnull(x) else str(x).strip()))

        # ✅ 사번, 이름 컬럼이 없다면 생성
        if "사번" not in df.columns:
            df["사번"] = ""
        if "이름" not in df.columns:
            df["이름"] = ""

        # ✅ 검색 적용
        if search_value and search_column in df.columns:
            df = df[df[search_column].astype(str).str.contains(search_value, case=False, regex=False)]

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


@app.get("/infra", response_class=HTMLResponse)
async def infra_page(
    request: Request,
    selected_sheets: List[str] = Query(default_factory=list),
    filter_column: str = Query("지사"),
    filter_value: str = Query(""),
    db: Session = Depends(get_db)
):
    # ✅ 사용자 라벨 ↔ 실제 시트명 매핑 (고정된 다섯 가지 유형)
    SHEET_LABELS = {
        "전월 무선가동점": "전월가동(무선)",
        "전월 유선가동점": "전월가동(유선)",
        "2개월 무선 연속가동점": "2개월(무선)",
        "2개월 유선 연속가동점": "2개월(유선)",
        "전월 신규점": "전월신규점"
    }

    # ✅ StoreData 기준으로 접점코드 → 사번/이름 매핑
    code_map = get_code_to_user_mapping(db) or {}
    print("✅ code_map 생성 완료:", list(code_map.keys())[:5])

    tables = {}
    summary_data = []

    # ✅ 다섯 가지 유형 모두 요약에 표시
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

        # ✅ 데이터프레임 로드 및 컬럼 정리
        df = pd.read_json(BytesIO(data_entry.data.encode("utf-8")))
        df.columns = [col.strip().replace(" ", "_") for col in df.columns]

        # 접점코드 → 사번/이름 매핑
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

        # ✅ 필터 적용 (지사, 센터, 접점코드, 사번)
        filtered_df = df.copy()
        if filter_column and filter_value:
            if filter_column in filtered_df.columns:
                filtered_df = filtered_df[
                    filtered_df[filter_column].astype(str).str.contains(filter_value, case=False, na=False)
                ]
                print(f"✅ '{filter_column}' 필터 적용: {filter_value}")

        # ✅ 정렬: 전월무선 또는 전월유선 기준 내림차순 (존재할 경우)
        sort_columns = [col for col in ["전월무선", "전월유선"] if col in filtered_df.columns]
        if sort_columns:
            try:
                for col in sort_columns:
                    filtered_df[col] = pd.to_numeric(filtered_df[col], errors="coerce")
                filtered_df = filtered_df.sort_values(by=sort_columns, ascending=False, na_position="last")
                print(f"✅ {', '.join(sort_columns)} 기준 내림차순 정렬 완료")
            except Exception as e:
                print(f"❌ 정렬 오류: {e}")

        # ✅ 요약 데이터 생성 (검색된 데이터 기준)
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

        # ✅ 사용자가 선택한 시트만 출력 테이블에 추가
        if label in selected_sheets:
            tables[label] = filtered_df.to_html(classes="table table-striped", index=False, escape=False)

    # ✅ 기본적으로 '전월 무선가동점' 체크 (초기 진입 시)
    if not selected_sheets and not filter_value:
        selected_sheets.append("전월 무선가동점")

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

    # ✅ 기존 타이틀 읽기 (없으면 기본값)
    title = db.query(SiteSettings).first()
    current_title = title.title if title else ""

    return templates.TemplateResponse("update_title.html", {
        "request": request,
        "current_title": current_title
    })

@app.post("/admin/update-title")
async def update_title(request: Request, new_title: str = Form(...), db: Session = Depends(get_db)):
    # ✅ 관리자 권한 확인
    if request.session.get("user_role") != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")

    # ✅ 타이틀 업데이트 로직
    setting = db.query(SiteSettings).first()
    if setting:
        setting.title = new_title.strip()
    else:
        setting = SiteSettings(title=new_title.strip())
        db.add(setting)

    db.commit()  # ✅ 커밋 필수
    db.refresh(setting)  # ✅ 변경된 값 즉시 반영 확인
    print(f"✅ 업데이트된 타이틀: {setting.title}")  # ✅ 디버깅: 타이틀 확인

    return HTMLResponse("<script>alert('✅ 타이틀이 업데이트되었습니다!'); location.href='/main';</script>")
