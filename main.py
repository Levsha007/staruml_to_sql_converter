# ============================================================================
# ПРОГРАММА: Конвертер PlantUML диаграмм в SQL код для PostgreSQL
# Назначение: Парсит текстовое описание диаграммы в формате PlantUML
#            и генерирует SQL-скрипт для создания базы данных
# Автор: Разработано для дипломного проекта
# Версия: 1.0
# ============================================================================

# ----------------------------------------------------------------------------
# ИМПОРТ НЕОБХОДИМЫХ МОДУЛЕЙ
# ----------------------------------------------------------------------------
from fastapi import FastAPI, Request, HTTPException  # FastAPI - веб-фреймворк
from fastapi.responses import HTMLResponse            # Для отправки HTML страниц
from fastapi.middleware.cors import CORSMiddleware    # Для разрешения запросов с других доменов
import uvicorn                                         # ASGI сервер для запуска FastAPI
import re                                              # Регулярные выражения для парсинга
import zlib                                            # Сжатие данных для PlantUML кодирования
from typing import Dict, List, Tuple, Optional        # Типизация для строгости кода
from pydantic import BaseModel                          # Валидация входящих данных

# ============================================================================
# ИНИЦИАЛИЗАЦИЯ FASTAPI ПРИЛОЖЕНИЯ
# ============================================================================
app = FastAPI(title="PlantUML to SQL Converter")       # Создаем веб-приложение с заголовком

# Настраиваем CORS (Cross-Origin Resource Sharing)
# Это разрешает браузерам отправлять запросы к нашему API с любых сайтов
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # Разрешаем запросы с любых доменов
    allow_credentials=True,      # Разрешаем передачу куки и заголовков аутентификации
    allow_methods=["*"],         # Разрешаем все HTTP методы (GET, POST, и т.д.)
    allow_headers=["*"],         # Разрешаем все заголовки
)

# ============================================================================
# МОДЕЛИ ДАННЫХ (Pydantic)
# ============================================================================
class PlantUMLRequest(BaseModel):
    """
    Модель запроса к API.
    Ожидает JSON с полем plantuml_code, содержащим текст диаграммы.
    """
    plantuml_code: str

# ============================================================================
# КЛАССЫ ДЛЯ ХРАНЕНИЯ ПРОПАРСЕННОЙ ИНФОРМАЦИИ
# ============================================================================

class Entity:
    """
    Класс для хранения информации об одной сущности (таблице).
    
    Атрибуты:
        name (str): Техническое имя сущности (идентификатор, латиница)
        display_name (str): Отображаемое имя (может быть на русском)
        attributes (list): Список атрибутов, каждый элемент - кортеж (имя, тип, is_pk, is_fk, is_uk)
        pk (list): Список имен атрибутов, входящих в первичный ключ
        uk (list): Список имен атрибутов, имеющих ограничение UNIQUE
        fk (list): Список имен атрибутов, являющихся внешними ключами
    """
    
    def __init__(self, name: str, display_name: str = ""):
        """
        Конструктор класса Entity.
        
        Параметры:
            name: Техническое имя сущности (например, "User2")
            display_name: Отображаемое имя (например, "Пользователь")
        """
        self.name = name
        self.display_name = display_name
        # Список атрибутов. Каждый атрибут хранится как кортеж:
        # (имя_атрибута, тип_данных, признак_PK, признак_FK, признак_UK)
        self.attributes = []
        self.pk = []    # Список полей первичного ключа
        self.uk = []    # Список полей с уникальным ключом
        self.fk = []    # Список полей с внешним ключом
    
    def add_attribute(self, name: str, data_type: str, is_pk: bool = False, 
                      is_fk: bool = False, is_uk: bool = False):
        """
        Добавляет атрибут к сущности.
        
        Параметры:
            name: Имя атрибута (например, "id", "имя")
            data_type: Тип данных из PlantUML (например, "int", "string")
            is_pk: Является ли атрибут частью первичного ключа
            is_fk: Является ли атрибут внешним ключом
            is_uk: Имеет ли атрибут ограничение UNIQUE
        """
        # Сохраняем атрибут в общем списке
        self.attributes.append((name, data_type, is_pk, is_fk, is_uk))
        
        # Если это первичный ключ, добавляем имя в список pk
        if is_pk:
            self.pk.append(name)
        
        # Если это уникальный ключ, добавляем имя в список uk
        if is_uk:
            self.uk.append(name)
        
        # Если это внешний ключ, добавляем имя в список fk
        if is_fk:
            self.fk.append(name)


class Relationship:
    """
    Класс для хранения информации о связи между сущностями.
    
    Атрибуты:
        from_entity (str): Имя исходной сущности (откуда идет связь)
        to_entity (str): Имя целевой сущности (куда идет связь)
        relation_type (str): Тип связи в нотации PlantUML (например, "||--o{")
        label (str): Подпись к связи (например, "использует")
    """
    
    def __init__(self, from_entity: str, to_entity: str, relation_type: str, label: str = ""):
        self.from_entity = from_entity
        self.to_entity = to_entity
        self.relation_type = relation_type
        self.label = label

# ============================================================================
# ПАРСЕР PLANTUML
# ============================================================================

class PlantUMLParser:
    """
    Парсер для разбора текста диаграммы в формате PlantUML.
    
    Читает построчно текст диаграммы, извлекает:
    - Сущности (entity) и их атрибуты
    - Связи (relationships) между сущностями
    - Отслеживает связи многие-ко-многим для создания junction-таблиц
    """
    
    def __init__(self, plantuml_code: str):
        """
        Конструктор парсера.
        
        Параметры:
            plantuml_code: Полный текст диаграммы PlantUML
        """
        self.code = plantuml_code                       # Исходный код диаграммы
        self.entities: Dict[str, Entity] = {}           # Словарь сущностей (ключ - имя)
        self.relationships: List[Relationship] = []     # Список связей
        self.many_to_many = []                           # Список пар для many-to-many
    
    def parse(self):
        """
        Основной метод парсинга.
        Разбивает код на строки и последовательно обрабатывает каждую.
        
        Возвращает:
            Кортеж (entities, relationships, many_to_many)
        """
        # Разбиваем код на строки, удаляем лишние пробелы в начале и конце
        lines = self.code.strip().split('\n')
        current_entity = None  # Текущая обрабатываемая сущность
        
        # Проходим по каждой строке диаграммы
        for line in lines:
            line = line.strip()  # Убираем пробелы по краям
            
            # Пропускаем комментарии (начинаются с ') и служебные директивы PlantUML
            if line.startswith("'") or line.startswith("@startuml") or line.startswith("@enduml"):
                continue
            
            # -----------------------------------------------------------------
            # ПОИСК СУЩНОСТИ (ENTITY)
            # -----------------------------------------------------------------
            # Проверяем несколько форматов записи сущности:
            
            # 1. Формат: entity "Отображаемое имя" as Идентификатор {
            entity_match = re.match(r'entity\s+"([^"]+)"\s+as\s+(\w+)\s*{', line)
            
            if not entity_match:
                # 2. Формат: entity Идентификатор as Идентификатор {
                entity_match = re.match(r'entity\s+(\w+)\s+as\s+(\w+)\s*{', line)
            
            if not entity_match:
                # 3. Формат: entity Идентификатор {
                entity_match = re.match(r'entity\s+(\w+)\s*{', line)
            
            if entity_match:
                # Определяем, сколько групп захватило регулярное выражение
                if len(entity_match.groups()) == 2:
                    # Есть и отображаемое имя, и идентификатор
                    display_name = entity_match.group(1)  # Отображаемое имя
                    entity_name = entity_match.group(2)   # Технический идентификатор
                else:
                    # Только одно имя (используем его и для отображения, и как идентификатор)
                    display_name = entity_match.group(1)
                    entity_name = entity_match.group(1)
                
                # Создаем новую сущность и делаем её текущей
                current_entity = Entity(entity_name, display_name)
                self.entities[entity_name] = current_entity
                continue  # Переходим к следующей строке
            
            # -----------------------------------------------------------------
            # ПОИСК АТРИБУТОВ (если мы внутри сущности)
            # -----------------------------------------------------------------
            if current_entity:
                # Пробуем найти атрибут с ограничениями в формате <<PK, FK>>
                # Пример: +id : int <<PK>>
                attr_match = re.match(r'\s*\+?([\w_]+)\s*:\s*(\w+).*?<<(.*?)>>', line)
                
                if attr_match:
                    # Извлекаем имя, тип и ограничения
                    attr_name = attr_match.group(1)      # Имя атрибута
                    attr_type = attr_match.group(2)      # Тип данных
                    constraints = attr_match.group(3)    # Строка с ограничениями (например "PK, FK")
                    
                    # Проверяем наличие каждого типа ограничений
                    is_pk = 'PK' in constraints          # Есть ли PK в строке ограничений
                    is_fk = 'FK' in constraints          # Есть ли FK в строке ограничений
                    is_uk = 'UK' in constraints          # Есть ли UK в строке ограничений
                    
                    # Добавляем атрибут к текущей сущности
                    current_entity.add_attribute(attr_name, attr_type, is_pk, is_fk, is_uk)
                else:
                    # Если нет ограничений, ищем простой атрибут
                    # Пример: имя : string
                    simple_match = re.match(r'\s*\+?([\w_]+)\s*:\s*(\w+)', line)
                    
                    if simple_match and '--' not in line:  # Проверяем, что это не разделитель
                        attr_name = simple_match.group(1)
                        attr_type = simple_match.group(2)
                        # Добавляем атрибут без ключей
                        current_entity.add_attribute(attr_name, attr_type, False, False, False)
                    elif '--' in line:
                        # Встретили разделитель -- просто пропускаем, он нужен только для визуала
                        pass
            
            # -----------------------------------------------------------------
            # ПОИСК СВЯЗЕЙ
            # -----------------------------------------------------------------
            # Ищем строки вида: User2 ||--o{ Device2 : использует
            # Группы: (from_entity) (символы_связи) (to_entity) (опционально: label)
            rel_match = re.match(r'(\w+)\s*([\|}o][o\|]{0,2}--[o\|]{0,2}[\|o{]?)\s*(\w+)(?:\s*:\s*"?(.*?)"?)?', line)
            
            if rel_match:
                from_entity = rel_match.group(1)        # Исходная сущность
                rel_type = rel_match.group(2)            # Тип связи (символы)
                to_entity = rel_match.group(3)           # Целевая сущность
                # Извлекаем подпись, если она есть (группа 4 может отсутствовать)
                label = rel_match.group(4) if len(rel_match.groups()) >= 4 else ""
                
                # Проверяем, что обе сущности существуют в нашей модели
                if from_entity in self.entities and to_entity in self.entities:
                    # Создаем объект связи и добавляем в список
                    rel = Relationship(from_entity, to_entity, rel_type, label)
                    self.relationships.append(rel)
                    
                    # Особый случай: связь многие-ко-многим
                    # Такие связи требуют создания отдельной junction-таблицы
                    if '}o--o{' in rel_type:
                        self.many_to_many.append((from_entity, to_entity))
        
        # Возвращаем результаты парсинга
        return self.entities, self.relationships, self.many_to_many

# ============================================================================
# ГЕНЕРАТОР SQL КОДА ДЛЯ POSTGRESQL
# ============================================================================

class SQLGenerator:
    """
    Генератор SQL-кода на основе пропарсенной модели.
    Преобразует сущности, атрибуты и связи в корректный DDL для PostgreSQL.
    """
    
    def __init__(self, entities: Dict[str, Entity], relationships: List[Relationship], 
                 many_to_many: List[Tuple]):
        """
        Конструктор генератора SQL.
        
        Параметры:
            entities: Словарь сущностей (результат парсинга)
            relationships: Список связей (результат парсинга)
            many_to_many: Список пар для many-to-many (результат парсинга)
        """
        self.entities = entities
        self.relationships = relationships
        self.many_to_many = many_to_many
    
    def _map_type(self, plantuml_type: str) -> str:
        """
        Преобразует тип из PlantUML в тип PostgreSQL.
        
        Параметры:
            plantuml_type: Тип из диаграммы (например, "int", "string", "datetime")
        
        Возвращает:
            Соответствующий тип PostgreSQL (например, "INTEGER", "VARCHAR(255)", "TIMESTAMP")
        """
        # Словарь соответствия типов
        mapping = {
            # Числовые типы
            'int': 'INTEGER',
            'integer': 'INTEGER',
            
            # Строковые типы
            'string': 'VARCHAR(255)',
            'varchar': 'VARCHAR(255)',
            'text': 'TEXT',
            
            # Дата и время
            'datetime': 'TIMESTAMP',
            'timestamp': 'TIMESTAMP',
            'date': 'DATE',
            
            # Логический тип
            'boolean': 'BOOLEAN',
            'bool': 'BOOLEAN',
            
            # Специальные типы
            'enum': 'VARCHAR(50)',      # В PostgreSQL можно создать ENUM, но пока так
            
            # Числа с плавающей точкой
            'float': 'FLOAT',
            'double': 'DOUBLE PRECISION',
            'decimal': 'DECIMAL(10,2)'
        }
        
        # Если тип не найден, по умолчанию используем VARCHAR(255)
        # .get() с вторым аргументом возвращает значение по умолчанию
        return mapping.get(plantuml_type.lower(), 'VARCHAR(255)')
    
    def _quote_ident(self, name: str) -> str:
        """
        Экранирует идентификаторы (имена таблиц и колонок) для PostgreSQL.
        
        Некоторые слова (user, group, table) являются зарезервированными в SQL.
        Чтобы использовать их как имена таблиц, нужно заключать в кавычки.
        
        Параметры:
            name: Имя таблицы или колонки
            
        Возвращает:
            Имя, возможно заключенное в кавычки
        """
        # Множество зарезервированных слов PostgreSQL (неполное, но основные)
        reserved_keywords = {'user', 'group', 'table', 'column', 'index', 
                            'foreign', 'primary', 'key'}
        
        # Если имя в нижнем регистре совпадает с зарезервированным словом
        if name.lower() in reserved_keywords:
            return f'"{name}"'  # Заключаем в кавычки
        
        # Иначе оставляем как есть
        return name
    
    def _get_pk_columns(self, entity_name: str) -> List[str]:
        """
        Возвращает список колонок, входящих в первичный ключ сущности.
        
        Параметры:
            entity_name: Имя сущности
            
        Возвращает:
            Список имен колонок (в нижнем регистре)
        """
        entity = self.entities.get(entity_name)
        if not entity:
            return []  # Сущность не найдена
        
        # Приводим все имена к нижнему регистру для единообразия
        return [pk.lower() for pk in entity.pk]
    
    def _get_pk_column(self, entity_name: str) -> Optional[str]:
        """
        Возвращает единственную колонку первичного ключа.
        
        Используется для простых случаев, когда первичный ключ состоит из одной колонки.
        Для составных ключей возвращает None, так как с ними нужна особая обработка.
        
        Параметры:
            entity_name: Имя сущности
            
        Возвращает:
            Имя колонки или None (если ключ составной или отсутствует)
        """
        pk_cols = self._get_pk_columns(entity_name)
        if len(pk_cols) == 1:
            return pk_cols[0]  # Ровно одна колонка в PK
        return None  # Нет PK или составной ключ
    
    def _determine_parent_child(self, rel: Relationship) -> Tuple[Optional[str], Optional[str]]:
        """
        Определяет, какая сущность является родительской, а какая дочерней.
        
        В PlantUML направление связи может быть разным, но для генерации внешних ключей
        нам нужно знать, где "один" (родитель), а где "много" (потомок).
        
        Параметры:
            rel: Объект связи
            
        Возвращает:
            Кортеж (родитель, потомок) или (None, None) если не удалось определить
        """
        rel_type = rel.relation_type
        
        # Случай 1: Связь категоризации (супертип-подтип)
        # Родитель слева (||), потомки справа (o|)
        # Пример: User ||--o| Regular
        if rel_type.startswith('||') and 'o|' in rel_type:
            return rel.from_entity, rel.to_entity
        
        # Случай 2: Один-ко-многим, родитель слева (||), потомок справа (o{)
        # Пример: User2 ||--o{ Device2
        if rel_type.startswith('||') and 'o{' in rel_type:
            return rel.from_entity, rel.to_entity
        
        # Случай 3: Один-ко-многим, родитель справа (||), потомок слева (o{)
        # Пример: Device2 o{--|| User2 (редкий случай)
        if 'o{' in rel_type and rel_type.endswith('||'):
            return rel.to_entity, rel.from_entity
        
        # Случай 4: Категоризация, родитель справа (||), потомки слева (o|)
        if 'o|' in rel_type and rel_type.endswith('||'):
            return rel.to_entity, rel.from_entity
        
        # Не удалось определить
        return None, None
    
    def generate(self) -> str:
        """
        Основной метод генерации SQL-кода.
        
        Процесс:
        1. Создание junction-таблиц для many-to-many связей
        2. Создание основных таблиц
        3. Добавление внешних ключей
        4. Специальная обработка для таблицы participants
        
        Возвращает:
            Полный SQL-скрипт для создания базы данных
        """
        sql = []  # Список строк SQL-кода
        
        # Заголовок сгенерированного файла
        sql.append("-- SQL код для PostgreSQL")
        sql.append("-- Сгенерировано из PlantUML диаграммы\n")
        
        # --------------------------------------------------------------------
        # ЭТАП 1: СОЗДАНИЕ JUNCTION-ТАБЛИЦ ДЛЯ MANY-TO-MANY
        # --------------------------------------------------------------------
        # Словарь для отслеживания созданных junction-таблиц
        # Ключ: имя таблицы, значение: кортеж (сущность1, сущность2)
        junction_tables = {}
        
        # Множество для отслеживания обработанных пар (чтобы не дублировать)
        junction_map = {}
        
        for from_ent, to_ent in self.many_to_many:
            # Сортируем имена для консистентности (чтобы не создать две таблицы
            # для одной и той же пары в разном порядке)
            pair_key = tuple(sorted([from_ent, to_ent]))
            
            if pair_key in junction_map:
                continue  # Эта пара уже обработана
            junction_map[pair_key] = True
            
            # Сортируем для определения имени таблицы
            entities = sorted([from_ent, to_ent])
            table_name = f"{entities[0]}_{entities[1]}".lower()
            junction_tables[table_name] = (entities[0], entities[1])
            
            # Генерируем SQL для junction-таблицы
            sql.append(f"-- Связь многие-ко-многим между {entities[0]} и {entities[1]}")
            sql.append(f"CREATE TABLE {self._quote_ident(table_name)} (")
            sql.append(f"    {self._quote_ident(entities[0].lower() + '_id')} INTEGER NOT NULL,")
            sql.append(f"    {self._quote_ident(entities[1].lower() + '_id')} INTEGER NOT NULL,")
            sql.append(f"    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,")
            sql.append(f"    PRIMARY KEY ({self._quote_ident(entities[0].lower() + '_id')}, "
                      f"{self._quote_ident(entities[1].lower() + '_id')})")
            sql.append(f");\n")
        
        # --------------------------------------------------------------------
        # ЭТАП 2: СОЗДАНИЕ ОСНОВНЫХ ТАБЛИЦ
        # --------------------------------------------------------------------
        for entity_name, entity in self.entities.items():
            # Пропускаем, если это уже созданная junction-таблица
            if any(entity_name.lower() == j.lower() for j in junction_tables.keys()):
                continue
            
            table_name = entity_name.lower()  # Имя таблицы в нижнем регистре
            
            sql.append(f"-- Таблица: {entity.display_name if entity.display_name else entity_name}")
            sql.append(f"CREATE TABLE {self._quote_ident(table_name)} (")
            
            attrs_sql = []  # Список SQL-определений атрибутов
            
            for attr_name, attr_type, is_pk, is_fk, is_uk in entity.attributes:
                sql_type = self._map_type(attr_type)  # Конвертируем тип
                col_name = attr_name.lower()           # Имя колонки в нижнем регистре
                
                # Обработка ограничения NOT NULL
                # Первичные ключи всегда NOT NULL
                if is_pk:
                    null_constraint = "NOT NULL"
                else:
                    null_constraint = "NULL"
                
                # PRIMARY KEY для одиночного ключа (добавляется к колонке)
                pk_constraint = ""
                if is_pk and len(entity.pk) == 1:
                    pk_constraint = " PRIMARY KEY"
                
                # UNIQUE ограничение
                unique_constraint = " UNIQUE" if is_uk else ""
                
                # Автоинкремент для PostgreSQL
                # Если это первичный ключ с именем 'id' и он одиночный
                auto_increment = ""
                if is_pk and len(entity.pk) == 1 and attr_name.lower() == 'id':
                    auto_increment = " GENERATED BY DEFAULT AS IDENTITY"
                
                # Собираем определение колонки
                attr_sql = f"    {self._quote_ident(col_name)} {sql_type}{auto_increment} {null_constraint}{pk_constraint}{unique_constraint}"
                attrs_sql.append(attr_sql)
            
            # Если есть составной PRIMARY KEY (несколько колонок)
            if len(entity.pk) > 1:
                pk_attrs = ", ".join([self._quote_ident(pk.lower()) for pk in entity.pk])
                attrs_sql.append(f"    PRIMARY KEY ({pk_attrs})")
            
            # Объединяем все определения через запятую с переводом строки
            sql.append(",\n".join(attrs_sql))
            sql.append(f");\n")
        
        # --------------------------------------------------------------------
        # ЭТАП 3: ДОБАВЛЕНИЕ ВНЕШНИХ КЛЮЧЕЙ
        # --------------------------------------------------------------------
        sql.append("-- Внешние ключи")
        foreign_keys_added = set()  # Множество для отслеживания добавленных FK
        
        # Сначала добавляем внешние ключи для обычных связей
        for rel in self.relationships:
            parent, child = self._determine_parent_child(rel)
            
            if parent and child and parent in self.entities and child in self.entities:
                # -------------------------------------------------------------
                # СЛУЧАЙ А: Связь категоризации (супертип-подтип)
                # -------------------------------------------------------------
                if 'o|' in rel.relation_type:
                    # Проверяем, что у родителя есть простой PK (не составной)
                    parent_pk = self._get_pk_column(parent)
                    if parent_pk:
                        fk_key = f"{child}_{parent}"
                        if fk_key not in foreign_keys_added:
                            sql.append(f"\n-- Связь категоризации: {parent} -> {child}")
                            sql.append(f"ALTER TABLE {self._quote_ident(child.lower())} "
                                      f"ADD CONSTRAINT fk_{child.lower()}_{parent.lower()}")
                            sql.append(f"    FOREIGN KEY ({self._quote_ident(parent_pk)}) "
                                      f"REFERENCES {self._quote_ident(parent.lower())}"
                                      f"({self._quote_ident(parent_pk)}) ON DELETE CASCADE;")
                            foreign_keys_added.add(fk_key)
                
                # -------------------------------------------------------------
                # СЛУЧАЙ Б: Обычная связь один-ко-многим
                # -------------------------------------------------------------
                elif 'o{' in rel.relation_type:
                    # Ищем колонку внешнего ключа в дочерней таблице
                    fk_column = None
                    
                    # Перебираем атрибуты дочерней сущности, помеченные как FK
                    for attr_name, _, _, is_fk, _ in self.entities[child].attributes:
                        if is_fk:
                            # Проверяем разные варианты именования
                            # Часто внешний ключ содержит имя родительской таблицы
                            if parent.lower() in attr_name.lower() or 'id' in attr_name.lower():
                                fk_column = attr_name.lower()
                                break
                    
                    # Если не нашли подходящий атрибут, используем стандартное имя
                    if not fk_column:
                        fk_column = f"{parent.lower()}_id"
                    
                    # Проверяем, что у родителя есть простой PK
                    parent_pk = self._get_pk_column(parent)
                    if parent_pk:
                        fk_key = f"{child}_{parent}"
                        if fk_key not in foreign_keys_added:
                            sql.append(f"\n-- Связь один-ко-многим: {parent} -> {child}")
                            sql.append(f"ALTER TABLE {self._quote_ident(child.lower())} "
                                      f"ADD CONSTRAINT fk_{child.lower()}_{parent.lower()}")
                            sql.append(f"    FOREIGN KEY ({self._quote_ident(fk_column)}) "
                                      f"REFERENCES {self._quote_ident(parent.lower())}"
                                      f"({self._quote_ident(parent_pk)}) ON DELETE CASCADE;")
                            foreign_keys_added.add(fk_key)
        
        # --------------------------------------------------------------------
        # ЭТАП 4: ВНЕШНИЕ КЛЮЧИ ДЛЯ JUNCTION-ТАБЛИЦ
        # --------------------------------------------------------------------
        for table_name, (ent1, ent2) in junction_tables.items():
            sql.append(f"\n-- Внешние ключи для связи {ent1} и {ent2}")
            
            # Внешний ключ к первой сущности
            pk1 = self._get_pk_column(ent1)
            if pk1:
                fk_key1 = f"{table_name}_{ent1}"
                if fk_key1 not in foreign_keys_added:
                    sql.append(f"ALTER TABLE {self._quote_ident(table_name)} "
                              f"ADD CONSTRAINT fk_{table_name}_{ent1.lower()}")
                    sql.append(f"    FOREIGN KEY ({self._quote_ident(ent1.lower() + '_id')}) "
                              f"REFERENCES {self._quote_ident(ent1.lower())}"
                              f"({self._quote_ident(pk1)}) ON DELETE CASCADE;")
                    foreign_keys_added.add(fk_key1)
            
            # Внешний ключ ко второй сущности
            pk2 = self._get_pk_column(ent2)
            if pk2:
                fk_key2 = f"{table_name}_{ent2}"
                if fk_key2 not in foreign_keys_added:
                    sql.append(f"ALTER TABLE {self._quote_ident(table_name)} "
                              f"ADD CONSTRAINT fk_{table_name}_{ent2.lower()}")
                    sql.append(f"    FOREIGN KEY ({self._quote_ident(ent2.lower() + '_id')}) "
                              f"REFERENCES {self._quote_ident(ent2.lower())}"
                              f"({self._quote_ident(pk2)}) ON DELETE CASCADE;")
                    foreign_keys_added.add(fk_key2)
        
        # --------------------------------------------------------------------
        # ЭТАП 5: СПЕЦИАЛЬНАЯ ОБРАБОТКА ДЛЯ ТАБЛИЦЫ PARTICIPANTS
        # --------------------------------------------------------------------
        # Это частный случай для нашей предметной области
        if 'participants' in self.entities:
            entity = self.entities['participants']
            # Проверяем, что у participants составной ключ (user_id, room_id)
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
                    sql.append(f"ALTER TABLE {self._quote_ident('participants')} "
                              f"ADD CONSTRAINT fk_participants_users")
                    sql.append(f"    FOREIGN KEY ({self._quote_ident('user_id')}) "
                              f"REFERENCES {self._quote_ident('users')}({self._quote_ident('id')}) "
                              f"ON DELETE CASCADE;")
                    foreign_keys_added.add("fk_participants_users")
                
                if has_rooms_fk and "fk_participants_rooms" not in str(foreign_keys_added):
                    sql.append(f"ALTER TABLE {self._quote_ident('participants')} "
                              f"ADD CONSTRAINT fk_participants_rooms")
                    sql.append(f"    FOREIGN KEY ({self._quote_ident('room_id')}) "
                              f"REFERENCES {self._quote_ident('rooms')}({self._quote_ident('id')}) "
                              f"ON DELETE CASCADE;")
                    foreign_keys_added.add("fk_participants_rooms")
        
        # Объединяем все строки SQL в один текст
        return "\n".join(sql)


# ============================================================================
# ФУНКЦИЯ КОДИРОВАНИЯ ДЛЯ PLANTUML
# ============================================================================

def encode_plantuml(text: str) -> str:
    """
    Кодирует текст диаграммы в формат, понятный PlantUML серверу.
    
    PlantUML использует специальное кодирование на основе сжатия deflate
    и последующего преобразования в 6-битные символы.
    
    Параметры:
        text: Исходный текст диаграммы
        
    Возвращает:
        Закодированную строку для URL
    """
    # ------------------------------------------------------------------------
    # ШАГ 1: Сжатие deflate (без заголовка zlib)
    # ------------------------------------------------------------------------
    # zlib.compress добавляет 2 байта заголовка и 4 байта контрольной суммы в конце
    # Нам нужны только сжатые данные без этих заголовков
    # [2:-4] удаляет первые 2 байта и последние 4 байта
    compressed = zlib.compress(text.encode("utf-8"))[2:-4]
    
    # ------------------------------------------------------------------------
    # ШАГ 2: Преобразование 6-битное кодирование
    # ------------------------------------------------------------------------
    # PlantUML использует свой алфавит для представления 6-битных значений
    def encode6bit(b):
        """
        Преобразует 6-битное число (0-63) в символ согласно алфавиту PlantUML.
        
        Алфавит: 0-9, A-Z, a-z, -, _
        """
        if b < 10:                     # 0-9 -> '0'-'9'
            return chr(48 + b)
        b -= 10
        if b < 26:                     # 10-35 -> 'A'-'Z'
            return chr(65 + b)
        b -= 26
        if b < 26:                     # 36-61 -> 'a'-'z'
            return chr(97 + b)
        b -= 26
        if b == 0:                      # 62 -> '-'
            return '-'
        if b == 1:                      # 63 -> '_'
            return '_'
        return '?'                      # Не должно произойти
    
    def append3bytes(b1, b2, b3):
        """
        Преобразует 3 байта (24 бита) в 4 символа по 6 бит.
        
        Схема:
        b1 (8 бит) = aaaaaaaa
        b2 (8 бит) = bbbbbbbb
        b3 (8 бит) = cccccccc
        
        Результат:
        c1 = первые 6 бит b1
        c2 = последние 2 бита b1 + первые 4 бита b2
        c3 = последние 4 бита b2 + первые 2 бита b3
        c4 = последние 6 бит b3
        """
        # c1 = первые 6 бит b1 (сдвигаем вправо на 2)
        c1 = b1 >> 2
        
        # c2 = (последние 2 бита b1) сдвинутые влево на 4 + (первые 4 бита b2 сдвинутые вправо на 4)
        c2 = ((b1 & 0x3) << 4) | (b2 >> 4)
        
        # c3 = (последние 4 бита b2) сдвинутые влево на 2 + (первые 2 бита b3 сдвинутые вправо на 6)
        c3 = ((b2 & 0xF) << 2) | (b3 >> 6)
        
        # c4 = последние 6 бит b3
        c4 = b3 & 0x3F
        
        # Преобразуем каждое 6-битное число в символ
        return (
            encode6bit(c1 & 0x3F) +
            encode6bit(c2 & 0x3F) +
            encode6bit(c3 & 0x3F) +
            encode6bit(c4 & 0x3F)
        )
    
    # Обрабатываем сжатые данные блоками по 3 байта
    res = ""
    i = 0
    while i < len(compressed):
        b1 = compressed[i]
        b2 = compressed[i + 1] if i + 1 < len(compressed) else 0
        b3 = compressed[i + 2] if i + 2 < len(compressed) else 0
        res += append3bytes(b1, b2, b3)
        i += 3
    
    return res


# ============================================================================
# HTML ШАБЛОН ДЛЯ ПОЛЬЗОВАТЕЛЬСКОГО ИНТЕРФЕЙСА
# ============================================================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PlantUML to SQL Converter</title>
    <style>
        /* 
           ТЕМНАЯ ТЕМА ИНТЕРФЕЙСА
           Цветовая схема вдохновлена редакторами кода (Nord theme)
        */
        
        /* Сброс стандартных отступов для всех элементов */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        /* Основные настройки страницы */
        body {
            font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Consolas', monospace;
            background: #1a1a1a;      /* Тёмный фон */
            color: #e0e0e0;            /* Светлый текст */
            min-height: 100vh;          /* Минимальная высота = весь экран */
            padding: 20px;
        }
        
        /* Контейнер для центрирования содержимого */
        .container {
            max-width: 1800px;
            margin: 0 auto;
        }
        
        /* Заголовок страницы */
        h1 {
            text-align: center;
            margin-bottom: 25px;
            font-weight: 400;
            font-size: 2em;
            color: #88c0d0;             /* Голубой цвет для заголовка */
            letter-spacing: 1px;
            border-bottom: 1px solid #3b4252;
            padding-bottom: 15px;
        }
        
        /* Панель с примерами диаграмм */
        .examples-panel {
            background: #2e3440;          /* Тёмно-синий фон */
            border: 1px solid #3b4252;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 20px;
        }
        
        /* Заголовок панели примеров */
        .examples-title {
            color: #88c0d0;
            margin-bottom: 12px;
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
        /* Сетка для кнопок примеров */
        .examples-grid {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }
        
        /* Кнопки примеров */
        .example-btn {
            background: #3b4252;
            border: 1px solid #434c5e;
            color: #e5e9f0;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-family: inherit;
            transition: all 0.2s;        /* Плавная анимация при наведении */
        }
        
        /* Эффект при наведении на кнопку */
        .example-btn:hover {
            background: #434c5e;
            border-color: #88c0d0;
            color: #88c0d0;
        }
        
        /* Основная сетка из трех панелей */
        .main-panel {
            display: grid;
            grid-template-columns: 1fr 1.5fr 1fr;  /* Центральная панель шире */
            gap: 20px;
            margin-bottom: 20px;
        }
        
        /* Базовая панель */
        .panel {
            background: #2e3440;
            border: 1px solid #3b4252;
            border-radius: 8px;
            display: flex;
            flex-direction: column;
            height: 650px;               /* Фиксированная высота */
        }
        
        /* Заголовок панели */
        .panel-header {
            padding: 12px 16px;
            border-bottom: 1px solid #3b4252;
            background: #3b4252;
            border-radius: 8px 8px 0 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        /* Текст в заголовке панели */
        .panel-header h3 {
            font-weight: 400;
            font-size: 13px;
            color: #e5e9f0;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        /* Контейнер для кнопок в заголовке */
        .panel-actions {
            display: flex;
            gap: 8px;
        }
        
        /* Кнопки действий в заголовке */
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
        
        /* Контентная область панели */
        .panel-content {
            flex: 1;                      /* Занимает всё оставшееся место */
            overflow: hidden;              /* Скрываем переполнение */
            background: #1a1a1a;
        }
        
        /* Поле ввода PlantUML кода */
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
            resize: none;                  /* Запрещаем изменение размера */
            outline: none;                  /* Убираем обводку при фокусе */
        }
        
        /* Область вывода SQL кода */
        .sql-output {
            height: 100%;
            overflow: auto;                 /* Добавляем прокрутку */
            background: #1a1a1a;
            color: #a3be8c;                 /* Зеленоватый цвет для SQL */
            padding: 16px;
            font-family: 'SF Mono', Monaco, 'Consolas', monospace;
            font-size: 13px;
            line-height: 1.6;
            white-space: pre-wrap;          /* Сохраняем форматирование */
        }
        
        /* Контейнер для диаграммы */
        .diagram-container {
            height: 100%;
            overflow: auto;
            background: #ffffff;            /* Белый фон для диаграммы */
            display: flex;
            justify-content: center;
            align-items: flex-start;
            padding: 20px;
        }
        
        /* Изображение диаграммы */
        .diagram-container img {
            max-width: 100%;
            height: auto;
            border-radius: 4px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }
        
        /* Строка состояния */
        .status-bar {
            background: #434c5e;
            color: #e5e9f0;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 13px;
            text-align: right;
        }
        
        /* Сообщение об ошибке */
        .error-message {
            color: #bf616a;                 /* Красный цвет для ошибок */
            padding: 16px;
            background: #3b4252;
            border-left: 3px solid #bf616a;
            margin: 16px;
            font-family: monospace;
            white-space: pre-wrap;
        }
        
        /* Индикатор загрузки */
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
        
        <!-- Панель с примерами диаграмм -->
        <div class="examples-panel">
            <div class="examples-title">📐 ПРИМЕРЫ ДИАГРАММ</div>
            <div class="examples-grid">
                <button class="example-btn" id="example1">Роли пользователей</button>
                <button class="example-btn" id="example2">Видеоконференции (EN)</button>
                <button class="example-btn" id="example3">Видеоконференции (RU)</button>
            </div>
        </div>
        
        <!-- Основные три панели -->
        <div class="main-panel">
            <!-- Левая панель: ввод PlantUML -->
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
            
            <!-- Центральная панель: отображение диаграммы (увеличенная) -->
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
            
            <!-- Правая панель: вывод SQL -->
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
        
        <!-- Строка состояния -->
        <div class="status-bar" id="statusBar">
            Готов к работе
        </div>
    </div>

    <script>
        // --------------------------------------------------------------------
        // КЛИЕНТСКАЯ ЛОГИКА (JavaScript)
        // --------------------------------------------------------------------
        
        // Примеры диаграмм для быстрого старта
        const EXAMPLES = {
            // Пример 1: Категоризация пользователей (супертип/подтип)
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
            
            // Пример 2: Система видеоконференций на английском
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
            
            // Пример 3: Система видеоконференций на русском
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

        // Получаем ссылки на элементы DOM
        const plantumlInput = document.getElementById('plantumlInput');
        const sqlOutput = document.getElementById('sqlOutput');
        const diagramContainer = document.getElementById('diagramContainer');
        const statusBar = document.getElementById('statusBar');

        // --------------------------------------------------------------------
        // Функция: renderDiagram - отображение диаграммы
        // Отправляет PlantUML код на сервер, получает URL изображения
        // --------------------------------------------------------------------
        async function renderDiagram() {
            const code = plantumlInput.value;
            if (!code.trim()) {
                diagramContainer.innerHTML = '<div class="loading">Введите PlantUML код</div>';
                return;
            }

            // Показываем индикатор загрузки
            diagramContainer.innerHTML = '<div class="loading">Загрузка диаграммы...</div>';
            
            try {
                // Отправляем POST запрос на /render
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
                    // Создаем элемент img с полученным URL
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

        // --------------------------------------------------------------------
        // Функция: convertToSQL - конвертация PlantUML в SQL
        // Отправляет код на сервер и получает сгенерированный SQL
        // --------------------------------------------------------------------
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

        // --------------------------------------------------------------------
        // Функция: copySQL - копирование SQL в буфер обмена
        // --------------------------------------------------------------------
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

        // --------------------------------------------------------------------
        // Функция: downloadSQL - скачивание SQL файла
        // --------------------------------------------------------------------
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

        // --------------------------------------------------------------------
        // Функция: loadExample - загрузка примера диаграммы
        // --------------------------------------------------------------------
        function loadExample(exampleKey) {
            plantumlInput.value = EXAMPLES[exampleKey];
            renderDiagram();
            convertToSQL();
            updateStatus('Пример загружен');
        }

        // --------------------------------------------------------------------
        // Функция: clearAll - очистка всех полей
        // --------------------------------------------------------------------
        function clearAll() {
            plantumlInput.value = '';
            diagramContainer.innerHTML = '<div class="loading">Введите PlantUML код</div>';
            sqlOutput.textContent = '-- SQL код появится после конвертации';
            updateStatus('Готов к работе');
        }

        // --------------------------------------------------------------------
        // Функция: updateStatus - обновление строки состояния
        // --------------------------------------------------------------------
        function updateStatus(message, isError = false) {
            statusBar.textContent = message;
            statusBar.style.background = isError ? '#bf616a' : '#434c5e';
        }

        // --------------------------------------------------------------------
        // НАЗНАЧЕНИЕ ОБРАБОТЧИКОВ СОБЫТИЙ
        // --------------------------------------------------------------------
        document.getElementById('example1').addEventListener('click', () => loadExample('example1'));
        document.getElementById('example2').addEventListener('click', () => loadExample('example2'));
        document.getElementById('example3').addEventListener('click', () => loadExample('example3'));
        document.getElementById('renderBtn').addEventListener('click', renderDiagram);
        document.getElementById('copyBtn').addEventListener('click', copySQL);
        document.getElementById('downloadBtn').addEventListener('click', downloadSQL);
        document.getElementById('clearBtn').addEventListener('click', clearAll);

        // При загрузке страницы показываем первый пример
        loadExample('example1');
    </script>
</body>
</html>"""


# ============================================================================
# API ЭНДПОЙНТЫ FASTAPI
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """
    Корневой эндпойнт, возвращает HTML-интерфейс.
    """
    return HTML_TEMPLATE


@app.post("/convert")
async def convert(request: PlantUMLRequest):
    """
    Эндпойнт для конвертации PlantUML в SQL.
    
    Ожидает JSON с полем plantuml_code.
    Возвращает JSON с полем sql.
    """
    try:
        # Парсим PlantUML код
        parser = PlantUMLParser(request.plantuml_code)
        entities, relationships, many_to_many = parser.parse()
        
        # Проверяем, что удалось распознать хотя бы одну сущность
        if not entities:
            raise HTTPException(status_code=400, detail="Не удалось распознать сущности")
        
        # Генерируем SQL
        generator = SQLGenerator(entities, relationships, many_to_many)
        sql = generator.generate()
        
        return {"sql": sql}
    
    except Exception as e:
        # В случае любой ошибки возвращаем 400 с текстом ошибки
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/render")
async def render(request: PlantUMLRequest):
    """
    Эндпойнт для получения URL диаграммы.
    
    Ожидает JSON с полем plantuml_code.
    Возвращает JSON с полем image_url для отображения диаграммы.
    """
    try:
        # Кодируем код для PlantUML сервера
        encoded = encode_plantuml(request.plantuml_code)
        
        # Формируем URL к бесплатному публичному серверу PlantUML
        image_url = f"https://www.plantuml.com/plantuml/png/{encoded}"
        
        return {"image_url": image_url}
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# ТОЧКА ВХОДА В ПРИЛОЖЕНИЕ
# ============================================================================
if __name__ == "__main__":
    """
    Запуск приложения при выполнении файла напрямую.
    """
    uvicorn.run(
        app, 
        host="127.0.0.1",  # Локальный хост
        port=8000,          # Порт 8000
        log_level="info"    # Уровень логирования
    )