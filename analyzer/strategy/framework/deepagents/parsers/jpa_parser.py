"""
JPA Parser (결정론적)

JPA/Hibernate 어노테이션 파싱.
@Entity, @Table, @Column 등 처리.

사용 예시:
    parser = JPAParser()
    result = parser.parse_entity_class(content)
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
import re


@dataclass
class JPAColumn:
    """JPA Column"""
    field_name: str
    column_name: str
    java_type: str = ""
    nullable: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_name": self.field_name,
            "column_name": self.column_name,
            "java_type": self.java_type,
            "nullable": self.nullable,
        }


@dataclass
class JPARelation:
    """JPA Relation"""
    field_name: str
    relation_type: str  # OneToOne | OneToMany | ManyToOne | ManyToMany
    target_entity: str = ""
    mapped_by: str = ""
    join_column: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_name": self.field_name,
            "relation_type": self.relation_type,
            "target_entity": self.target_entity,
            "mapped_by": self.mapped_by,
            "join_column": self.join_column,
        }


@dataclass
class JPAEntity:
    """JPA Entity"""
    class_name: str
    table_name: str
    schema: str = ""
    catalog: str = ""
    columns: List[JPAColumn] = field(default_factory=list)
    relations: List[JPARelation] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "class_name": self.class_name,
            "table_name": self.table_name,
            "schema": self.schema,
            "catalog": self.catalog,
            "columns": [c.to_dict() for c in self.columns],
            "relations": [r.to_dict() for r in self.relations],
        }


@dataclass
class JPAParseResult:
    """JPA 파싱 결과"""
    entity: Optional[JPAEntity]
    is_entity: bool
    annotations: List[str]
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity": self.entity.to_dict() if self.entity else None,
            "is_entity": self.is_entity,
            "annotations": self.annotations,
            "errors": self.errors,
        }


class JPAParser:
    """JPA 어노테이션 파서 (결정론적)"""
    
    # 관계 어노테이션
    RELATION_ANNOTATIONS = ['OneToOne', 'OneToMany', 'ManyToOne', 'ManyToMany']
    
    def parse_entity_class(self, content: str) -> JPAParseResult:
        """
        JPA Entity 클래스 파싱
        
        Args:
            content: Java 소스 코드
        
        Returns:
            JPAParseResult
        """
        annotations = []
        errors = []
        
        # 1. @Entity 확인
        is_entity = bool(re.search(r'@Entity\b', content))
        if not is_entity:
            return JPAParseResult(
                entity=None,
                is_entity=False,
                annotations=[],
            )
        
        annotations.append('@Entity')
        
        # 2. 클래스명 추출
        class_match = re.search(r'class\s+(\w+)', content)
        class_name = class_match.group(1) if class_match else ""
        
        # 3. @Table 파싱
        table_name = class_name  # 기본값: 클래스명
        schema = ""
        catalog = ""
        
        table_match = re.search(
            r'@Table\s*\(\s*(?:[^)]*?name\s*=\s*["\']([^"\']+)["\'])?'
            r'(?:[^)]*?schema\s*=\s*["\']([^"\']+)["\'])?'
            r'(?:[^)]*?catalog\s*=\s*["\']([^"\']+)["\'])?',
            content, re.DOTALL
        )
        if table_match:
            annotations.append('@Table')
            if table_match.group(1):
                table_name = table_match.group(1)
            if table_match.group(2):
                schema = table_match.group(2)
            if table_match.group(3):
                catalog = table_match.group(3)
        
        # 4. 컬럼 파싱
        columns = self._parse_columns(content)
        
        # 5. 관계 파싱
        relations = self._parse_relations(content)
        
        for rel in relations:
            annotations.append(f'@{rel.relation_type}')
        
        entity = JPAEntity(
            class_name=class_name,
            table_name=table_name,
            schema=schema,
            catalog=catalog,
            columns=columns,
            relations=relations,
        )
        
        return JPAParseResult(
            entity=entity,
            is_entity=True,
            annotations=list(set(annotations)),
            errors=errors,
        )
    
    def _parse_columns(self, content: str) -> List[JPAColumn]:
        """@Column 파싱"""
        columns = []
        
        # @Column(name="...") 패턴
        pattern = r'@Column\s*\(\s*(?:[^)]*?name\s*=\s*["\']([^"\']+)["\'])?[^)]*\)\s*(?:private|protected|public)?\s*(\w+)\s+(\w+)'
        
        for match in re.finditer(pattern, content, re.DOTALL):
            col_name = match.group(1)
            java_type = match.group(2)
            field_name = match.group(3)
            
            columns.append(JPAColumn(
                field_name=field_name,
                column_name=col_name or field_name,  # 없으면 필드명
                java_type=java_type,
            ))
        
        # @Id 필드도 추출
        id_pattern = r'@Id\s*(?:@[\w]+\s*(?:\([^)]*\)\s*)*)*(?:private|protected|public)?\s*(\w+)\s+(\w+)'
        for match in re.finditer(id_pattern, content, re.DOTALL):
            java_type = match.group(1)
            field_name = match.group(2)
            
            # 중복 체크
            if not any(c.field_name == field_name for c in columns):
                columns.append(JPAColumn(
                    field_name=field_name,
                    column_name=field_name,  # @Id는 보통 컬럼명 = 필드명
                    java_type=java_type,
                ))
        
        return columns
    
    def _parse_relations(self, content: str) -> List[JPARelation]:
        """관계 어노테이션 파싱"""
        relations = []
        
        for rel_type in self.RELATION_ANNOTATIONS:
            # @OneToMany(targetEntity=..., mappedBy=...)
            pattern = rf'@{rel_type}\s*\(([^)]*)\)\s*(?:private|protected|public)?\s*(?:[\w<>,\s]+)\s+(\w+)'
            
            for match in re.finditer(pattern, content, re.DOTALL):
                params = match.group(1)
                field_name = match.group(2)
                
                # targetEntity 추출
                target_match = re.search(r'targetEntity\s*=\s*(\w+)', params)
                target_entity = target_match.group(1) if target_match else ""
                
                # mappedBy 추출
                mapped_match = re.search(r'mappedBy\s*=\s*["\']([^"\']+)["\']', params)
                mapped_by = mapped_match.group(1) if mapped_match else ""
                
                relations.append(JPARelation(
                    field_name=field_name,
                    relation_type=rel_type,
                    target_entity=target_entity,
                    mapped_by=mapped_by,
                ))
        
        # @JoinColumn도 체크
        join_pattern = r'@JoinColumn\s*\(\s*(?:[^)]*?name\s*=\s*["\']([^"\']+)["\'])[^)]*\)\s*(?:private|protected|public)?\s*(?:[\w<>,\s]+)\s+(\w+)'
        for match in re.finditer(join_pattern, content, re.DOTALL):
            join_col = match.group(1)
            field_name = match.group(2)
            
            # 기존 relation에 join_column 추가
            for rel in relations:
                if rel.field_name == field_name:
                    rel.join_column = join_col
                    break
        
        return relations
    
    def extract_table_name(self, content: str) -> Optional[str]:
        """
        간단하게 테이블명만 추출
        
        Args:
            content: Java 소스 코드
        
        Returns:
            테이블명 또는 None
        """
        # @Table(name="...")
        table_match = re.search(r'@Table\s*\([^)]*name\s*=\s*["\']([^"\']+)["\']', content)
        if table_match:
            return table_match.group(1)
        
        # @Entity 있으면 클래스명 사용
        if re.search(r'@Entity\b', content):
            class_match = re.search(r'class\s+(\w+)', content)
            if class_match:
                return class_match.group(1)
        
        return None

