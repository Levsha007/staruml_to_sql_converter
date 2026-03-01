import re
from typing import Dict, List, Tuple, Optional

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