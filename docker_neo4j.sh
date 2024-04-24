docker run \
	-d \
	--restart always \
	--publish=7474:7474 --publish=7687:7687 \
	--env NEO4J_AUTH=$NEO4J_USER/$NEO4J_PASS \
	-e NEO4J_apoc_export_file_enabled=true \
	-e NEO4J_apoc_import_file_enabled=true \
	-e NEO4J_apoc_import_file_use__neo4j__config=true \
	-e NEO4J_PLUGINS=\[\"apoc\"\] \
	--volume=$PWD/neo4j/data:/data \
	neo4j
