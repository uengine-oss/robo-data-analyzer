"""
OFBiz Parser (결정론적)

OFBiz entitydefs/servicedef XML 파싱.

사용 예시:
    parser = OFBizParser()
    result = parser.parse_entitydef("entitydefs/order.xml", content)
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
import re
import xml.etree.ElementTree as ET


@dataclass
class OFBizEntity:
    """OFBiz Entity"""
    entity_name: str
    table_name: str
    package_name: str = ""
    description: str = ""  # 테이블 description (title, description 속성에서)
    fields: List[Dict[str, str]] = field(default_factory=list)
    relations: List[Dict[str, str]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_name": self.entity_name,
            "table_name": self.table_name,
            "package_name": self.package_name,
            "description": self.description,
            "fields": self.fields,
            "relations": self.relations,
        }


@dataclass
class OFBizService:
    """OFBiz Service"""
    name: str
    engine: str
    location: str = ""
    invoke: str = ""
    default_entity_name: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "engine": self.engine,
            "location": self.location,
            "invoke": self.invoke,
            "default_entity_name": self.default_entity_name,
        }


@dataclass
class OFBizParseResult:
    """OFBiz 파싱 결과"""
    file_path: str
    entities: List[OFBizEntity]
    services: List[OFBizService]
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "entities": [e.to_dict() for e in self.entities],
            "services": [s.to_dict() for s in self.services],
            "errors": self.errors,
        }


class OFBizParser:
    """OFBiz XML 파서 (결정론적)"""
    
    def parse_entitydef(
        self, 
        file_path: str, 
        content: str
    ) -> OFBizParseResult:
        """
        entitydefs XML 파싱
        
        Args:
            file_path: 파일 경로
            content: XML 내용
        
        Returns:
            OFBizParseResult
        """
        entities = []
        errors = []
        
        try:
            root = ET.fromstring(content)
            
            # entity 추출
            for entity_elem in root.findall('.//entity'):
                entity = self._parse_entity(entity_elem)
                if entity:
                    entities.append(entity)
            
            # view-entity 추출 (조인 뷰)
            for view_elem in root.findall('.//view-entity'):
                entity = self._parse_view_entity(view_elem)
                if entity:
                    entities.append(entity)
        
        except ET.ParseError as e:
            errors.append(f"XML parse error: {e}")
            entities = self._regex_fallback_entities(content)
        
        return OFBizParseResult(
            file_path=file_path,
            entities=entities,
            services=[],
            errors=errors,
        )
    
    def parse_servicedef(
        self, 
        file_path: str, 
        content: str
    ) -> OFBizParseResult:
        """
        servicedef XML 파싱
        
        Args:
            file_path: 파일 경로
            content: XML 내용
        
        Returns:
            OFBizParseResult
        """
        services = []
        errors = []
        
        try:
            root = ET.fromstring(content)
            
            # service 추출
            for service_elem in root.findall('.//service'):
                service = self._parse_service(service_elem)
                if service:
                    services.append(service)
        
        except ET.ParseError as e:
            errors.append(f"XML parse error: {e}")
            services = self._regex_fallback_services(content)
        
        return OFBizParseResult(
            file_path=file_path,
            entities=[],
            services=services,
            errors=errors,
        )
    
    def _parse_entity(self, elem: ET.Element) -> Optional[OFBizEntity]:
        """entity 요소 파싱"""
        entity_name = elem.attrib.get('entity-name', '')
        if not entity_name:
            return None
        
        table_name = elem.attrib.get('table-name', entity_name)
        package_name = elem.attrib.get('package-name', '')
        
        # description 추출 (title, description 속성 또는 하위 텍스트)
        description = elem.attrib.get('title', '') or elem.attrib.get('description', '')
        if not description:
            # 하위 description 요소 확인
            desc_elem = elem.find('description')
            if desc_elem is not None and desc_elem.text:
                description = desc_elem.text.strip()
        
        # fields 추출 (description 포함!)
        fields = []
        for field_elem in elem.findall('.//field'):
            field_desc = field_elem.attrib.get('description', '')
            # 하위 description 요소도 확인
            if not field_desc:
                field_desc_elem = field_elem.find('description')
                if field_desc_elem is not None and field_desc_elem.text:
                    field_desc = field_desc_elem.text.strip()
            
            fields.append({
                'name': field_elem.attrib.get('name', ''),
                'type': field_elem.attrib.get('type', ''),
                'col-name': field_elem.attrib.get('col-name', ''),
                'description': field_desc,
            })
        
        # relations 추출
        relations = []
        for rel_elem in elem.findall('.//relation'):
            relations.append({
                'type': rel_elem.attrib.get('type', ''),
                'rel-entity-name': rel_elem.attrib.get('rel-entity-name', ''),
                'fk-name': rel_elem.attrib.get('fk-name', ''),
            })
        
        return OFBizEntity(
            entity_name=entity_name,
            table_name=table_name,
            package_name=package_name,
            description=description,
            fields=fields,
            relations=relations,
        )
    
    def _parse_view_entity(self, elem: ET.Element) -> Optional[OFBizEntity]:
        """view-entity 요소 파싱"""
        entity_name = elem.attrib.get('entity-name', '')
        if not entity_name:
            return None
        
        # view-entity는 table이 없고 member-entity로 구성
        member_entities = []
        for member in elem.findall('.//member-entity'):
            member_entities.append(member.attrib.get('entity-alias', ''))
        
        return OFBizEntity(
            entity_name=entity_name,
            table_name="",  # view는 직접 테이블 없음
            package_name=elem.attrib.get('package-name', ''),
            relations=[{'type': 'view-member', 'rel-entity-name': m} for m in member_entities],
        )
    
    def _parse_service(self, elem: ET.Element) -> Optional[OFBizService]:
        """service 요소 파싱"""
        name = elem.attrib.get('name', '')
        if not name:
            return None
        
        engine = elem.attrib.get('engine', '')
        location = elem.attrib.get('location', '')
        invoke = elem.attrib.get('invoke', '')
        default_entity = elem.attrib.get('default-entity-name')
        
        return OFBizService(
            name=name,
            engine=engine,
            location=location,
            invoke=invoke,
            default_entity_name=default_entity,
        )
    
    def _regex_fallback_entities(self, content: str) -> List[OFBizEntity]:
        """Entity regex fallback"""
        entities = []
        
        pattern = r'<entity[^>]*entity-name\s*=\s*["\']([^"\']+)["\'][^>]*(?:table-name\s*=\s*["\']([^"\']+)["\'])?'
        for match in re.finditer(pattern, content, re.IGNORECASE):
            entity_name = match.group(1)
            table_name = match.group(2) or entity_name
            
            entities.append(OFBizEntity(
                entity_name=entity_name,
                table_name=table_name,
            ))
        
        return entities
    
    def _regex_fallback_services(self, content: str) -> List[OFBizService]:
        """Service regex fallback"""
        services = []
        
        pattern = r'<service[^>]*name\s*=\s*["\']([^"\']+)["\'][^>]*engine\s*=\s*["\']([^"\']+)["\']'
        for match in re.finditer(pattern, content, re.IGNORECASE):
            services.append(OFBizService(
                name=match.group(1),
                engine=match.group(2),
            ))
        
        return services

