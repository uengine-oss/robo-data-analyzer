from analyzer.common.ddl_parser import parse_ddl


def _get_table(result: dict, schema: str, name: str) -> dict:
    key_schema = schema.lower()
    key_name = name.lower()
    for table_info in result.get("analysis", []):
        table = table_info.get("table", {})
        if table.get("schema", "").lower() == key_schema and table.get("name", "").lower() == key_name:
            return table_info
    raise AssertionError(f"table not found: {schema}.{name}")


def test_parse_alter_table_mixed_actions_foreign_keys():
    ddl = """
    CREATE TABLE IF NOT EXISTS steel_safety.master_equipment (
      equip_id VARCHAR(20) PRIMARY KEY
    );

    CREATE TABLE IF NOT EXISTS steel_safety.master_sensor_tags (
      tag_id VARCHAR(30) PRIMARY KEY,
      equip_id VARCHAR(20)
    );

    CREATE TABLE IF NOT EXISTS steel_safety.timeseries_sensor_data (
      "timestamp" TIMESTAMP,
      tag_id VARCHAR(30),
      equip_id VARCHAR(20)
    );

    ALTER TABLE steel_safety.timeseries_sensor_data
      DROP CONSTRAINT IF EXISTS fk_timeseries_sensor_data_tag,
      ADD CONSTRAINT fk_timeseries_sensor_data_tag
        FOREIGN KEY (tag_id) REFERENCES steel_safety.master_sensor_tags(tag_id),
      DROP CONSTRAINT IF EXISTS fk_timeseries_sensor_data_equip,
      ADD CONSTRAINT fk_timeseries_sensor_data_equip
        FOREIGN KEY (equip_id) REFERENCES steel_safety.master_equipment(equip_id) NOT VALID;
    """

    result = parse_ddl(ddl)
    table_info = _get_table(result, "steel_safety", "timeseries_sensor_data")

    fks = table_info.get("foreignKeys", [])
    assert {"column": "tag_id", "ref": "steel_safety.master_sensor_tags.tag_id"} in fks
    assert {"column": "equip_id", "ref": "steel_safety.master_equipment.equip_id"} in fks


def test_parse_alter_table_add_primary_key_after_drop():
    ddl = """
    CREATE TABLE IF NOT EXISTS steel_safety.demo (
      id VARCHAR(20),
      ts TIMESTAMP
    );

    ALTER TABLE steel_safety.demo
      DROP CONSTRAINT IF EXISTS demo_pkey,
      ADD CONSTRAINT demo_pkey PRIMARY KEY (id, ts);
    """

    result = parse_ddl(ddl)
    table_info = _get_table(result, "steel_safety", "demo")
    assert table_info.get("primaryKeys") == ["id", "ts"]
