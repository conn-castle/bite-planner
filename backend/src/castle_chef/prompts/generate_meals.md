Create $count distinct meals for a family. Every meal must be low effort to cook.

${feedback}First check a compact batch of proposed names with `check_meal_titles` before writing recipes. Replace rejected ideas with different dishes, not just new names or sides. The tools enforce distinct ideas throughout this draft, even liked dishes, and a rolling month cooldown across earlier drafts except for positively rated dishes. Preflight reserves nothing; `add_meal` rechecks each name.

Write every recipe for $servings servings (the family chooses how many servings to make when they order), and add each meal with the castle-chef `add_meal` tool. Give each meal its `dish`, and give its main ingredients (what you'd see on the plate) an `icon` and `color` for its picture. If a tool reports an error, fix what it names and call it again. When you think all $count meals are added, call `done`, and fix what it reports until it says the batch is complete. Then reply with the meals' names.
