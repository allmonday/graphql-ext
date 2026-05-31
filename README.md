# graphql-ext

验证一种极简 GraphQL DSL 扩展方案：
- 移除 fragment、alias、变量定义
- 在内联字段注解中支持 `post` / `sendto` / `expose`
- 注解控制 resolve 管线而非混入 schema

注解语义：
- `post` — 字段 resolve 后的后处理 hook（对齐 pydantic-resolve 的 post_method）
- `sendto` — 将字段值传递给同级兄弟 resolver 作为上下文（对齐 send_to）
- `expose` — 将字段值暴露给后代 resolver，通过 ancestor_context 访问（对齐 expose_as）

核心模块：
- `parser.py` — 手写递归下降解析器，~100 行
- `resolver.py` — 执行引擎，调度注解
- `ast.py` — 精简 AST 节点定义
