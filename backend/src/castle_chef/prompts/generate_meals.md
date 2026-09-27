Create $count distinct meals for a family. Every meal must be low effort to cook.

${avoid}Add each meal with the castle-chef `add_meal` tool, and always set its `servings`. If a tool reports an error, fix what it names and call it again. When you think all $count meals are added, call `done`, and fix what it reports until it says the batch is complete. Then reply with the meals' names.
