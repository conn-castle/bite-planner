Create $count distinct meals for a family. Every meal must be low effort to cook.

${avoid}Add each meal by running this shell command once per meal, with the meal as JSON on stdin:

```sh
castle-chef meal add <<'EOF'
{"name": "...", "servings": 4, "ingredients": [...], "steps": [...]}
EOF
```

The JSON must match this JSON Schema:

```json
$meal_schema
```

Always set `servings`. If the command reports an error, fix the JSON and run it again for that meal; a meal that was saved must not be added again. Do not run any other commands. When all $count meals are added, reply with their names.
