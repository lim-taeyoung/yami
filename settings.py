# settings.py
import os

IS_LOCAL = os.path.exists(".local")  # .local 파일이 있으면 로컬로 판단

if IS_LOCAL:
    DATABASE_URL = "sqlite:///./excel_data.db"
else:
    DATABASE_URL = "postgresql://yami_user:ycBs0JgawTkWX2b1HhLki3YAKMBLStWX@dpg-d02etvuuk2gs73edlhog-a.oregon-postgres.render.com/yami?sslmode=require"
