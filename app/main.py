from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
from typing import Dict, Any

from app.plantuml_parser import PlantUMLParser
from app.sql_generator import SQLGenerator
from app.utils import encode_plantuml, HTML_TEMPLATE

app = FastAPI(
    title="PlantUML to SQL Converter",
    description="Конвертер PlantUML диаграмм в SQL код для PostgreSQL",
    version="1.0.0"
)

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Создаем директорию для статических файлов если её нет
os.makedirs("static", exist_ok=True)

class PlantUMLRequest(BaseModel):
    plantuml_code: str

@app.get("/", response_class=HTMLResponse)
async def root():
    """Главная страница"""
    return HTML_TEMPLATE

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {"status": "healthy", "service": "plantuml-sql-converter"}

@app.post("/api/convert")
async def convert(request: PlantUMLRequest) -> Dict[str, Any]:
    """Конвертация PlantUML в SQL"""
    try:
        if not request.plantuml_code or not request.plantuml_code.strip():
            raise HTTPException(status_code=400, detail="Пустой PlantUML код")
        
        parser = PlantUMLParser(request.plantuml_code)
        entities, relationships, many_to_many = parser.parse()
        
        if not entities:
            raise HTTPException(
                status_code=400, 
                detail="Не удалось распознать сущности. Проверьте синтаксис PlantUML"
            )
        
        generator = SQLGenerator(entities, relationships, many_to_many)
        sql = generator.generate()
        
        return {
            "success": True,
            "sql": sql,
            "entities_count": len(entities),
            "relationships_count": len(relationships)
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/render")
async def render(request: PlantUMLRequest) -> Dict[str, str]:
    """Рендеринг PlantUML диаграммы"""
    try:
        if not request.plantuml_code or not request.plantuml_code.strip():
            raise HTTPException(status_code=400, detail="Пустой PlantUML код")
        
        encoded = encode_plantuml(request.plantuml_code)
        image_url = f"https://www.plantuml.com/plantuml/png/{encoded}"
        
        return {"image_url": image_url}
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Обработчик ошибок"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "detail": exc.detail
        }
    )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )