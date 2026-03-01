from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import re
import zlib
import os
from typing import Dict, List, Tuple, Optional
from pydantic import BaseModel

app = FastAPI(title="PlantUML to SQL Converter")

# Health check для Render
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "plantuml-sql-converter"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class PlantUMLRequest(BaseModel):
    plantuml_code: str

class Entity:
    def __init__(self, name: str, display_name: str = ""):
        self.name = name
        self.display_name = display_name
        self.attributes = []  # (name, type, is_pk, is_fk, is_uk)
        self.pk = []  # список полей первичного ключа
        self.uk = []  # список уникальных ключей
        self.fk = []  # список внешних ключей
    
    def add_attribute(self, name: str, data_type: str, is_pk: bool = False, is_fk: bool = False, is_uk: bool = False):
        self.attributes.append((name, data_type, is_pk, is_fk, is_uk))
        if is_pk:
            self.pk.append(name)
        if is_uk:
            self.uk.append(name)
        if is_fk:
            self.fk.append(name)

class Relationship:
    def __init__(self, from_entity: str, to_entity: str, relation_type: str, label: str = ""):
        self.from_entity = from_entity
        self.to_entity = to_entity
        self.relation_type = relation_type
        self.label = label

class PlantUMLParser:
    def __init__(self, plantuml_code: str):
        self.code = plantuml_code
        self.entities: Dict[str, Entity] = {}
        self.relationships: List[Relationship] = []
        self.many_to_many = []
    
    def parse(self):
        lines = self.code.strip().split('\n')
        current_entity = None
        
        for line in lines:
            line = line.strip()
            
            if line.startswith("'") or line.startswith("@startuml") or line.startswith("@enduml"):
                continue
            
            # Поиск entity с русским названием
            entity_match = re.match(r'entity\s+"([^"]+)"\s+as\s+(\w+)\s*{', line)
            if not entity_match:
                entity_match = re.match(r'entity\s+(\w+)\s+as\s+(\w+)\s*{', line)
            if not entity_match:
                entity_match = re.match(r'entity\s+(\w+)\s*{', line)
            
            if entity_match:
                if len(entity_match.groups()) == 2:
                    display_name = entity_match.group(1)
                    entity_name = entity_match.group(2)
                else:
                    display_name = entity_match.group(1)
                    entity_name = entity_match.group(1)
                current_entity = Entity(entity_name, display_name)
                self.entities[entity_name] = current_entity
                continue
            
            # Поиск атрибутов
            if current_entity:
                # Формат: +имя : тип <<PK, FK>>
                attr_match = re.match(r'\s*\+?([\w_]+)\s*:\s*(\w+).*?<<(.*?)>>', line)
                if attr_match:
                    attr_name = attr_match.group(1)
                    attr_type = attr_match.group(2)
                    constraints = attr_match.group(3)
                    
                    is_pk = 'PK' in constraints
                    is_fk = 'FK' in constraints
                    is_uk = 'UK' in constraints
                    current_entity.add_attribute(attr_name, attr_type, is_pk, is_fk, is_uk)
                else:
                    # Формат без <<>> (простой атрибут)
                    simple_match = re.match(r'\s*\+?([\w_]+)\s*:\s*(\w+)', line)
                    if simple_match and '--' not in line:
                        attr_name = simple_match.group(1)
                        attr_type = simple_match.group(2)
                        current_entity.add_attribute(attr_name, attr_type, False, False, False)
                    elif '--' in line:
                        # Разделитель -- пропускаем
                        pass
            
            # Поиск связей
            rel_match = re.match(r'(\w+)\s*([\|}o][o\|]{0,2}--[o\|]{0,2}[\|o{]?)\s*(\w+)(?:\s*:\s*"?(.*?)"?)?', line)
            if rel_match:
                from_entity = rel_match.group(1)
                rel_type = rel_match.group(2)
                to_entity = rel_match.group(3)
                label = rel_match.group(4) if len(rel_match.groups()) >= 4 else ""
                
                if from_entity in self.entities and to_entity in self.entities:
                    rel = Relationship(from_entity, to_entity, rel_type, label)
                    self.relationships.append(rel)
                    
                    # Проверка на многие-ко-многим
                    if '}o--o{' in rel_type:
                        self.many_to_many.append((from_entity, to_entity))
        
        return self.entities, self.relationships, self.many_to_many

class SQLGenerator:
    def __init__(self, entities: Dict[str, Entity], relationships: List[Relationship], many_to_many: List[Tuple]):
        self.entities = entities
        self.relationships = relationships
        self.many_to_many = many_to_many
    
    def _map_type(self, plantuml_type: str) -> str:
        """Маппинг типов для PostgreSQL"""
        mapping = {
            'int': 'INTEGER',
            'integer': 'INTEGER',
            'string': 'VARCHAR(255)',
            'varchar': 'VARCHAR(255)',
            'text': 'TEXT',
            'datetime': 'TIMESTAMP',
            'timestamp': 'TIMESTAMP',
            'date': 'DATE',
            'boolean': 'BOOLEAN',
            'bool': 'BOOLEAN',
            'enum': 'VARCHAR(50)',
            'float': 'FLOAT',
            'double': 'DOUBLE PRECISION',
            'decimal': 'DECIMAL(10,2)'
        }
        return mapping.get(plantuml_type.lower(), 'VARCHAR(255)')
    
    def _quote_ident(self, name: str) -> str:
        """Экранирует идентификаторы для PostgreSQL"""
        # Список зарезервированных слов PostgreSQL
        reserved_keywords = {'user', 'group', 'table', 'column', 'index', 'foreign', 'primary', 'key'}
        if name.lower() in reserved_keywords:
            return f'"{name}"'
        return name
    
    def _get_pk_columns(self, entity_name: str) -> List[str]:
        """Возвращает список колонок первичного ключа сущности"""
        entity = self.entities.get(entity_name)
        if not entity:
            return []
        return [pk.lower() for pk in entity.pk]
    
    def _get_pk_column(self, entity_name: str) -> Optional[str]:
        """Возвращает единственную колонку первичного ключа или None если составной"""
        pk_cols = self._get_pk_columns(entity_name)
        if len(pk_cols) == 1:
            return pk_cols[0]
        return None
    
    def _determine_parent_child(self, rel: Relationship) -> Tuple[Optional[str], Optional[str]]:
        """Определяет родительскую и дочернюю сущности на основе типа связи"""
        rel_type = rel.relation_type
        
        # Связь категоризации: родитель слева (||), потомки справа (o|)
        if rel_type.startswith('||') and 'o|' in rel_type:
            return rel.from_entity, rel.to_entity
        
        # Связь один-ко-многим: родитель слева (||), потомок справа (o{)
        if rel_type.startswith('||') and 'o{' in rel_type:
            return rel.from_entity, rel.to_entity
        
        # Связь один-ко-многим: родитель справа (||), потомок слева (o{)
        if 'o{' in rel_type and rel_type.endswith('||'):
            return rel.to_entity, rel.from_entity
        
        # Связь категоризации: родитель справа (||), потомки слева (o|)
        if 'o|' in rel_type and rel_type.endswith('||'):
            return rel.to_entity, rel.from_entity
        
        return None, None
    
    def generate(self) -> str:
        sql = []
        
        sql.append("-- SQL код для PostgreSQL")
        sql.append("-- Сгенерировано из PlantUML диаграммы\n")
        
        # Словарь для отслеживания созданных junction-таблиц
        junction_tables = {}
        
        # Создаем таблицы для many-to-many
        junction_map = {}  # для отслеживания, какие пары уже обработаны
        for from_ent, to_ent in self.many_to_many:
            # Сортируем имена для консистентности
            pair_key = tuple(sorted([from_ent, to_ent]))
            if pair_key in junction_map:
                continue
            junction_map[pair_key] = True
            
            entities = sorted([from_ent, to_ent])
            table_name = f"{entities[0]}_{entities[1]}".lower()
            junction_tables[table_name] = (entities[0], entities[1])
            
            sql.append(f"-- Связь многие-ко-многим между {entities[0]} и {entities[1]}")
            sql.append(f"CREATE TABLE {self._quote_ident(table_name)} (")
            sql.append(f"    {self._quote_ident(entities[0].lower() + '_id')} INTEGER NOT NULL,")
            sql.append(f"    {self._quote_ident(entities[1].lower() + '_id')} INTEGER NOT NULL,")
            sql.append(f"    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,")
            sql.append(f"    PRIMARY KEY ({self._quote_ident(entities[0].lower() + '_id')}, {self._quote_ident(entities[1].lower() + '_id')})")
            sql.append(f");\n")
        
        # Основные таблицы
        for entity_name, entity in self.entities.items():
            # Проверяем, не является ли эта таблица junction-таблицей
            if any(entity_name.lower() == j.lower() for j in junction_tables.keys()):
                continue
            
            table_name = entity_name.lower()
            
            sql.append(f"-- Таблица: {entity.display_name if entity.display_name else entity_name}")
            sql.append(f"CREATE TABLE {self._quote_ident(table_name)} (")
            
            attrs_sql = []
            for attr_name, attr_type, is_pk, is_fk, is_uk in entity.attributes:
                sql_type = self._map_type(attr_type)
                col_name = attr_name.lower()
                
                # Обработка NULL/NOT NULL
                if is_pk:
                    null_constraint = "NOT NULL"
                else:
                    null_constraint = "NULL"
                
                # PRIMARY KEY для одиночного ключа
                pk_constraint = ""
                if is_pk and len(entity.pk) == 1:
                    pk_constraint = " PRIMARY KEY"
                
                # UNIQUE
                unique_constraint = " UNIQUE" if is_uk else ""
                
                # Автоинкремент для PostgreSQL
                auto_increment = ""
                if is_pk and len(entity.pk) == 1 and attr_name.lower() == 'id':
                    auto_increment = " GENERATED BY DEFAULT AS IDENTITY"
                
                attr_sql = f"    {self._quote_ident(col_name)} {sql_type}{auto_increment} {null_constraint}{pk_constraint}{unique_constraint}"
                attrs_sql.append(attr_sql)
            
            # Составной PRIMARY KEY
            if len(entity.pk) > 1:
                pk_attrs = ", ".join([self._quote_ident(pk.lower()) for pk in entity.pk])
                attrs_sql.append(f"    PRIMARY KEY ({pk_attrs})")
            
            sql.append(",\n".join(attrs_sql))
            sql.append(f");\n")
        
        # Внешние ключи
        sql.append("-- Внешние ключи")
        foreign_keys_added = set()
        
        # Сначала добавляем внешние ключи для обычных связей
        for rel in self.relationships:
            parent, child = self._determine_parent_child(rel)
            
            if parent and child and parent in self.entities and child in self.entities:
                # Для категоризации (подтипы наследуют PK от родителя)
                if 'o|' in rel.relation_type:
                    # Проверяем, что у родителя есть простой PK
                    parent_pk = self._get_pk_column(parent)
                    if parent_pk:
                        fk_key = f"{child}_{parent}"
                        if fk_key not in foreign_keys_added:
                            sql.append(f"\n-- Связь категоризации: {parent} -> {child}")
                            sql.append(f"ALTER TABLE {self._quote_ident(child.lower())} ADD CONSTRAINT fk_{child.lower()}_{parent.lower()}")
                            sql.append(f"    FOREIGN KEY ({self._quote_ident(parent_pk)}) REFERENCES {self._quote_ident(parent.lower())}({self._quote_ident(parent_pk)}) ON DELETE CASCADE;")
                            foreign_keys_added.add(fk_key)
                
                # Для связи один-ко-многим
                elif 'o{' in rel.relation_type:
                    # Ищем колонку внешнего ключа в дочерней таблице
                    fk_column = None
                    for attr_name, _, _, is_fk, _ in self.entities[child].attributes:
                        if is_fk:
                            # Проверяем разные варианты именования
                            if parent.lower() in attr_name.lower() or 'id' in attr_name.lower():
                                fk_column = attr_name.lower()
                                break
                    
                    # Если не нашли, используем стандартное имя
                    if not fk_column:
                        fk_column = f"{parent.lower()}_id"
                    
                    # Проверяем, что у родителя есть простой PK
                    parent_pk = self._get_pk_column(parent)
                    if parent_pk:
                        fk_key = f"{child}_{parent}"
                        if fk_key not in foreign_keys_added:
                            sql.append(f"\n-- Связь один-ко-многим: {parent} -> {child}")
                            sql.append(f"ALTER TABLE {self._quote_ident(child.lower())} ADD CONSTRAINT fk_{child.lower()}_{parent.lower()}")
                            sql.append(f"    FOREIGN KEY ({self._quote_ident(fk_column)}) REFERENCES {self._quote_ident(parent.lower())}({self._quote_ident(parent_pk)}) ON DELETE CASCADE;")
                            foreign_keys_added.add(fk_key)
        
        # Внешние ключи для junction-таблиц
        for table_name, (ent1, ent2) in junction_tables.items():
            sql.append(f"\n-- Внешние ключи для связи {ent1} и {ent2}")
            
            # Проверяем PK для первой сущности
            pk1 = self._get_pk_column(ent1)
            if pk1:
                fk_key1 = f"{table_name}_{ent1}"
                if fk_key1 not in foreign_keys_added:
                    sql.append(f"ALTER TABLE {self._quote_ident(table_name)} ADD CONSTRAINT fk_{table_name}_{ent1.lower()}")
                    sql.append(f"    FOREIGN KEY ({self._quote_ident(ent1.lower() + '_id')}) REFERENCES {self._quote_ident(ent1.lower())}({self._quote_ident(pk1)}) ON DELETE CASCADE;")
                    foreign_keys_added.add(fk_key1)
            
            # Проверяем PK для второй сущности
            pk2 = self._get_pk_column(ent2)
            if pk2:
                fk_key2 = f"{table_name}_{ent2}"
                if fk_key2 not in foreign_keys_added:
                    sql.append(f"ALTER TABLE {self._quote_ident(table_name)} ADD CONSTRAINT fk_{table_name}_{ent2.lower()}")
                    sql.append(f"    FOREIGN KEY ({self._quote_ident(ent2.lower() + '_id')}) REFERENCES {self._quote_ident(ent2.lower())}({self._quote_ident(pk2)}) ON DELETE CASCADE;")
                    foreign_keys_added.add(fk_key2)
        
        # Специальная обработка для participants (если существует)
        if 'participants' in self.entities:
            entity = self.entities['participants']
            # Проверяем, что у participants составной ключ
            if len(entity.pk) == 2:
                # Ищем внешние ключи к users и rooms
                has_users_fk = False
                has_rooms_fk = False
                
                for rel in self.relationships:
                    if rel.relation_type == '}o--o{':
                        if (rel.from_entity == 'users' and rel.to_entity == 'participants') or \
                           (rel.from_entity == 'participants' and rel.to_entity == 'users'):
                            has_users_fk = True
                        if (rel.from_entity == 'rooms' and rel.to_entity == 'participants') or \
                           (rel.from_entity == 'participants' and rel.to_entity == 'rooms'):
                            has_rooms_fk = True
                
                sql.append("\n-- Внешние ключи для participants")
                
                if has_users_fk and "fk_participants_users" not in str(foreign_keys_added):
                    sql.append(f"ALTER TABLE {self._quote_ident('participants')} ADD CONSTRAINT fk_participants_users")
                    sql.append(f"    FOREIGN KEY ({self._quote_ident('user_id')}) REFERENCES {self._quote_ident('users')}({self._quote_ident('id')}) ON DELETE CASCADE;")
                    foreign_keys_added.add("fk_participants_users")
                
                if has_rooms_fk and "fk_participants_rooms" not in str(foreign_keys_added):
                    sql.append(f"ALTER TABLE {self._quote_ident('participants')} ADD CONSTRAINT fk_participants_rooms")
                    sql.append(f"    FOREIGN KEY ({self._quote_ident('room_id')}) REFERENCES {self._quote_ident('rooms')}({self._quote_ident('id')}) ON DELETE CASCADE;")
                    foreign_keys_added.add("fk_participants_rooms")
        
        return "\n".join(sql)

def encode_plantuml(text: str) -> str:
    """Правильное кодирование для PlantUML"""
    # Raw deflate (без zlib-заголовка)
    compressed = zlib.compress(text.encode("utf-8"))[2:-4]
    
    def encode6bit(b):
        if b < 10:
            return chr(48 + b)
        b -= 10
        if b < 26:
            return chr(65 + b)
        b -= 26
        if b < 26:
            return chr(97 + b)
        b -= 26
        if b == 0:
            return '-'
        if b == 1:
            return '_'
        return '?'
    
    def append3bytes(b1, b2, b3):
        c1 = b1 >> 2
        c2 = ((b1 & 0x3) << 4) | (b2 >> 4)
        c3 = ((b2 & 0xF) << 2) | (b3 >> 6)
        c4 = b3 & 0x3F
        return (
            encode6bit(c1 & 0x3F)
            + encode6bit(c2 & 0x3F)
            + encode6bit(c3 & 0x3F)
            + encode6bit(c4 & 0x3F)
        )
    
    res = ""
    i = 0
    while i < len(compressed):
        b1 = compressed[i]
        b2 = compressed[i + 1] if i + 1 < len(compressed) else 0
        b3 = compressed[i + 2] if i + 2 < len(compressed) else 0
        res += append3bytes(b1, b2, b3)
        i += 3
    
    return res

# HTML шаблон
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PlantUML to SQL Converter</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Consolas', monospace;
            background: #1a1a1a;
            color: #e0e0e0;
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1800px;
            margin: 0 auto;
        }
        
        h1 {
            text-align: center;
            margin-bottom: 25px;
            font-weight: 400;
            font-size: 2em;
            color: #88c0d0;
            letter-spacing: 1px;
            border-bottom: 1px solid #3b4252;
            padding-bottom: 15px;
        }
        
        .examples-panel {
            background: #2e3440;
            border: 1px solid #3b4252;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 20px;
        }
        
        .examples-title {
            color: #88c0d0;
            margin-bottom: 12px;
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
        .examples-grid {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }
        
        .example-btn {
            background: #3b4252;
            border: 1px solid #434c5e;
            color: #e5e9f0;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-family: inherit;
            transition: all 0.2s;
        }
        
        .example-btn:hover {
            background: #434c5e;
            border-color: #88c0d0;
            color: #88c0d0;
        }
        
        .main-panel {
            display: grid;
            grid-template-columns: 1fr 1.5fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }
        
        .panel {
            background: #2e3440;
            border: 1px solid #3b4252;
            border-radius: 8px;
            display: flex;
            flex-direction: column;
            height: 650px;
        }
        
        .panel-header {
            padding: 12px 16px;
            border-bottom: 1px solid #3b4252;
            background: #3b4252;
            border-radius: 8px 8px 0 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .panel-header h3 {
            font-weight: 400;
            font-size: 13px;
            color: #e5e9f0;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .panel-actions {
            display: flex;
            gap: 8px;
        }
        
        .panel-actions button {
            background: #434c5e;
            border: none;
            color: #e5e9f0;
            padding: 4px 10px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            font-family: inherit;
        }
        
        .panel-actions button:hover {
            background: #4c566a;
            color: #88c0d0;
        }
        
        .panel-content {
            flex: 1;
            overflow: hidden;
            background: #1a1a1a;
        }
        
        textarea {
            width: 100%;
            height: 100%;
            padding: 16px;
            border: none;
            background: #1a1a1a;
            color: #e5e9f0;
            font-family: 'SF Mono', Monaco, 'Consolas', monospace;
            font-size: 13px;
            line-height: 1.6;
            resize: none;
            outline: none;
        }
        
        .sql-output {
            height: 100%;
            overflow: auto;
            background: #1a1a1a;
            color: #a3be8c;
            padding: 16px;
            font-family: 'SF Mono', Monaco, 'Consolas', monospace;
            font-size: 13px;
            line-height: 1.6;
            white-space: pre-wrap;
        }
        
        .diagram-container {
            height: 100%;
            overflow: auto;
            background: #ffffff;
            display: flex;
            justify-content: center;
            align-items: flex-start;
            padding: 20px;
        }
        
        .diagram-container img {
            max-width: 100%;
            height: auto;
            border-radius: 4px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }
        
        .status-bar {
            background: #434c5e;
            color: #e5e9f0;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 13px;
            text-align: right;
        }
        
        .error-message {
            color: #bf616a;
            padding: 16px;
            background: #3b4252;
            border-left: 3px solid #bf616a;
            margin: 16px;
            font-family: monospace;
            white-space: pre-wrap;
        }
        
        .loading {
            color: #88c0d0;
            padding: 20px;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>PLANTUML → SQL CONVERTER (PostgreSQL)</h1>
        
        <div class="examples-panel">
            <div class="examples-title">📐 ПРИМЕРЫ ДИАГРАММ</div>
            <div class="examples-grid">
                <button class="example-btn" id="example1">Роли пользователей</button>
                <button class="example-btn" id="example2">Видеоконференции (EN)</button>
                <button class="example-btn" id="example3">Видеоконференции (RU)</button>
            </div>
        </div>
        
        <div class="main-panel">
            <!-- Левая панель -->
            <div class="panel">
                <div class="panel-header">
                    <h3>📝 PLANTUML КОД</h3>
                    <div class="panel-actions">
                        <button id="clearBtn">Очистить</button>
                    </div>
                </div>
                <div class="panel-content">
                    <textarea id="plantumlInput" placeholder="Введите PlantUML код..."></textarea>
                </div>
            </div>
            
            <!-- Центральная панель (увеличенная) -->
            <div class="panel">
                <div class="panel-header">
                    <h3>🖼️ ДИАГРАММА</h3>
                    <div class="panel-actions">
                        <button id="renderBtn">Обновить</button>
                    </div>
                </div>
                <div class="panel-content">
                    <div id="diagramContainer" class="diagram-container">
                        <div class="loading">Введите код и нажмите "Обновить"</div>
                    </div>
                </div>
            </div>
            
            <!-- Правая панель -->
            <div class="panel">
                <div class="panel-header">
                    <h3>🗄️ SQL КОД</h3>
                    <div class="panel-actions">
                        <button id="copyBtn">Копировать</button>
                        <button id="downloadBtn">Скачать</button>
                    </div>
                </div>
                <div class="panel-content">
                    <div id="sqlOutput" class="sql-output">-- SQL код появится после конвертации</div>
                </div>
            </div>
        </div>
        
        <div class="status-bar" id="statusBar">
            Готов к работе
        </div>
    </div>

    <script>
        // Примеры диаграмм
        const EXAMPLES = {
            example1: `@startuml
' =============================
' Диаграмма 1 — Роли пользователя
' =============================

entity "Пользователь" as User {
  +id : int <<PK>>
  --
  имя : string
  email : string
  дата_регистрации : datetime
  тип_пользователя : string
}

entity "Постоянный" as Regular {
  +id : int <<PK, FK>>
  --
  последний_визит : datetime
  аватар : string
}

entity "Модератор" as Moderator {
  +id : int <<PK, FK>>
  --
  уровень_прав : int
  дата_назначения : datetime
}

entity "Гость" as Guest {
  +id : int <<PK, FK>>
  --
  срок_действия_ссылки : datetime
  организация : string
}

User ||--o| Regular
User ||--o| Moderator
User ||--o| Guest

@enduml`,
            
            example2: `@startuml
' =============================
' Диаграмма 2 — Система видеоконференций (EN)
' =============================

entity users {
  +id : int <<PK>>
  --
  username : varchar
  email : varchar <<UK>>
  password_hash : varchar
  created_at : timestamp
}

entity rooms {
  +id : int <<PK>>
  --
  name : varchar
  creator_id : int <<FK>>
  created_at : timestamp
  is_active : boolean
}

entity participants {
  +user_id : int <<PK, FK>>
  +room_id : int <<PK, FK>>
  --
  joined_at : timestamp
}

entity devices {
  +id : int <<PK>>
  --
  user_id : int <<FK>>
  device_type : enum
  device_name : varchar
}

entity messages {
  +id : int <<PK>>
  --
  content : text
  sent_at : timestamp
  sender_id : int <<FK>>
  room_id : int <<FK>>
}

entity media_streams {
  +id : int <<PK>>
  --
  sdp_info : text
  stream_type : enum
  user_id : int <<FK>>
  room_id : int <<FK>>
}

users ||--o{ devices
users ||--o{ messages
users ||--o{ media_streams
rooms ||--o{ messages
rooms ||--o{ media_streams
users }o--o{ participants
rooms }o--o{ participants

@enduml`,
            
            example3: `@startuml
' =============================
' Диаграмма 3 — Система видеоконференций (RU)
' =============================

entity "Пользователь" as User2 {
  +id : int <<PK>>
  --
  имя : string
  email : string
  пароль_хэш : string
  дата_регистрации : datetime
}

entity "Комната" as Room2 {
  +id : int <<PK>>
  --
  название : string
  создатель_id : int <<FK>>
  дата_создания : datetime
  активна : boolean
}

entity "Устройство" as Device2 {
  +id : int <<PK>>
  --
  пользователь_id : int <<FK>>
  тип : string
  название_устройства : string
}

entity "Сообщение" as Message2 {
  +id : int <<PK>>
  --
  текст : string
  время_отправки : datetime
  отправитель_id : int <<FK>>
  комната_id : int <<FK>>
}

entity "Медиа_поток" as Stream2 {
  +id : int <<PK>>
  --
  sdp_информация : string
  тип_потока : string
  пользователь_id : int <<FK>>
  комната_id : int <<FK>>
}

User2 ||--o{ Device2 : использует
User2 ||--o{ Message2 : отправляет
User2 ||--o{ Stream2 : генерирует
Room2 ||--o{ Message2 : содержит
Room2 ||--o{ Stream2 : содержит
User2 }o--o{ Room2 : участвует

@enduml`
        };

        const plantumlInput = document.getElementById('plantumlInput');
        const sqlOutput = document.getElementById('sqlOutput');
        const diagramContainer = document.getElementById('diagramContainer');
        const statusBar = document.getElementById('statusBar');

        async function renderDiagram() {
            const code = plantumlInput.value;
            if (!code.trim()) {
                diagramContainer.innerHTML = '<div class="loading">Введите PlantUML код</div>';
                return;
            }

            diagramContainer.innerHTML = '<div class="loading">Загрузка диаграммы...</div>';
            
            try {
                const response = await fetch('/render', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        plantuml_code: code
                    })
                });
                
                const data = await response.json();
                if (response.ok) {
                    const img = new Image();
                    img.onload = () => {
                        diagramContainer.innerHTML = '';
                        diagramContainer.appendChild(img);
                        updateStatus('Диаграмма загружена');
                    };
                    img.onerror = () => {
                        diagramContainer.innerHTML = '<div class="error-message">Ошибка загрузки диаграммы</div>';
                        updateStatus('Ошибка рендеринга', true);
                    };
                    img.src = data.image_url;
                    img.alt = 'PlantUML Diagram';
                    img.style.maxWidth = '100%';
                } else {
                    diagramContainer.innerHTML = `<div class="error-message">${data.detail}</div>`;
                    updateStatus('Ошибка', true);
                }
            } catch (error) {
                diagramContainer.innerHTML = `<div class="error-message">Ошибка: ${error.message}</div>`;
                updateStatus('Ошибка', true);
            }
        }

        async function convertToSQL() {
            const plantumlCode = plantumlInput.value;
            
            if (!plantumlCode.trim()) {
                sqlOutput.textContent = '-- Введите PlantUML код для конвертации';
                updateStatus('Ожидание ввода');
                return;
            }
            
            sqlOutput.textContent = '-- Конвертация...';
            updateStatus('Конвертация...');
            
            try {
                const response = await fetch('/convert', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        plantuml_code: plantumlCode
                    })
                });
                
                const data = await response.json();
                if (response.ok) {
                    sqlOutput.textContent = data.sql;
                    updateStatus('Конвертация завершена');
                } else {
                    sqlOutput.textContent = `-- Ошибка: ${data.detail}`;
                    updateStatus('Ошибка', true);
                }
            } catch (error) {
                sqlOutput.textContent = `-- Ошибка: ${error.message}`;
                updateStatus('Ошибка', true);
            }
        }

        function copySQL() {
            const sql = sqlOutput.textContent;
            if (sql && !sql.includes('Ошибка') && !sql.includes('Введите')) {
                navigator.clipboard.writeText(sql).then(() => {
                    updateStatus('SQL скопирован');
                }).catch(() => {
                    updateStatus('Ошибка копирования', true);
                });
            }
        }

        function downloadSQL() {
            const sql = sqlOutput.textContent;
            if (sql && !sql.includes('Ошибка') && !sql.includes('Введите')) {
                const blob = new Blob([sql], { type: 'text/plain' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'schema.sql';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                updateStatus('SQL скачан');
            }
        }

        function loadExample(exampleKey) {
            plantumlInput.value = EXAMPLES[exampleKey];
            renderDiagram();
            convertToSQL();
            updateStatus('Пример загружен');
        }

        function clearAll() {
            plantumlInput.value = '';
            diagramContainer.innerHTML = '<div class="loading">Введите PlantUML код</div>';
            sqlOutput.textContent = '-- SQL код появится после конвертации';
            updateStatus('Готов к работе');
        }

        function updateStatus(message, isError = false) {
            statusBar.textContent = message;
            statusBar.style.background = isError ? '#bf616a' : '#434c5e';
        }

        // Event listeners
        document.getElementById('example1').addEventListener('click', () => loadExample('example1'));
        document.getElementById('example2').addEventListener('click', () => loadExample('example2'));
        document.getElementById('example3').addEventListener('click', () => loadExample('example3'));
        document.getElementById('renderBtn').addEventListener('click', renderDiagram);
        document.getElementById('copyBtn').addEventListener('click', copySQL);
        document.getElementById('downloadBtn').addEventListener('click', downloadSQL);
        document.getElementById('clearBtn').addEventListener('click', clearAll);

        // Загружаем первый пример
        loadExample('example1');
    </script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML_TEMPLATE

@app.post("/convert")
async def convert(request: PlantUMLRequest):
    try:
        parser = PlantUMLParser(request.plantuml_code)
        entities, relationships, many_to_many = parser.parse()
        
        if not entities:
            raise HTTPException(status_code=400, detail="Не удалось распознать сущности")
        
        generator = SQLGenerator(entities, relationships, many_to_many)
        sql = generator.generate()
        
        return {"sql": sql}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/render")
async def render(request: PlantUMLRequest):
    try:
        encoded = encode_plantuml(request.plantuml_code)
        image_url = f"https://www.plantuml.com/plantuml/png/{encoded}"
        return {"image_url": image_url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")