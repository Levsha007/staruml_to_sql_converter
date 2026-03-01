from typing import Dict, List, Tuple, Optional

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
        sql.append("-- Сгенерировано из PlantUML диаграммы")
        sql.append("-- Дата генерации: " + __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        sql.append("")
        
        # Включаем поддержку внешних ключей
        sql.append("-- Включаем поддержку внешних ключей")
        sql.append("SET session_replication_role = 'origin';\n")
        
        # Удаляем таблицы если существуют (в обратном порядке зависимостей)
        sql.append("-- Удаление существующих таблиц")
        sql.append("DROP TABLE IF EXISTS participants CASCADE;")
        sql.append("DROP TABLE IF EXISTS messages CASCADE;")
        sql.append("DROP TABLE IF EXISTS media_streams CASCADE;")
        sql.append("DROP TABLE IF EXISTS devices CASCADE;")
        sql.append("DROP TABLE IF EXISTS regular_users CASCADE;")
        sql.append("DROP TABLE IF EXISTS moderators CASCADE;")
        sql.append("DROP TABLE IF EXISTS guests CASCADE;")
        sql.append("DROP TABLE IF EXISTS rooms CASCADE;")
        sql.append("DROP TABLE IF EXISTS users CASCADE;\n")
        
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
        
        # Создаем индексы
        sql.append("\n-- Индексы для оптимизации")
        sql.append("CREATE INDEX IF NOT EXISTS idx_messages_room_id ON messages(room_id);")
        sql.append("CREATE INDEX IF NOT EXISTS idx_messages_sent_at ON messages(sent_at);")
        sql.append("CREATE INDEX IF NOT EXISTS idx_media_streams_room_id ON media_streams(room_id);")
        sql.append("CREATE INDEX IF NOT EXISTS idx_participants_room_id ON participants(room_id);")
        sql.append("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")
        
        # Добавляем комментарии к таблицам
        sql.append("\n-- Комментарии к таблицам")
        for entity_name, entity in self.entities.items():
            if entity.display_name:
                sql.append(f"COMMENT ON TABLE {self._quote_ident(entity_name.lower())} IS '{entity.display_name}';")
        
        return "\n".join(sql)