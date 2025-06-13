from pydantic import BaseModel

class StoreEntry(BaseModel):
    접점코드: str
    접점명: str
    이름: str
    사번: str
    지사: str
    센터: str
    주소: str
