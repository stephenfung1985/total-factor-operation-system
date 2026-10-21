-- 钻井单井经营管控系统 · SQLite 表结构
-- 依据《钻井单井经营管控系统 核心数据库表设计》与《主逻辑线》，本版只建“算对”主线所需的表。
-- 金额单位元，单价保留四位小数；经济事项是全系统唯一存金额的表，任何接口都不能直接写它。

PRAGMA foreign_keys = ON;

-- ---------- 一、主数据 ----------

CREATE TABLE IF NOT EXISTS app_user (
  user_id     TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  role        TEXT NOT NULL CHECK (role IN ('finance', 'team', 'dept')),
  who         TEXT NOT NULL,          -- 界面左下角的身份说明
  team_id     TEXT,                   -- 基层队账号：按队伍过滤
  dept        TEXT,                   -- 专业部室账号：按归口部室过滤（市场/技术/物资/装备/安全/人事）
  scope_label TEXT NOT NULL
);
-- 登录名、密码散列等列由 db._add_columns 补齐（兼容旧库）

-- 登录会话：只存令牌的散列
-- 手工删除的井：经济事项只冲不改，删除 = 冲销该井全部有效事项 + 在此登记；井从各页面隐藏，再次导入或登记时恢复
CREATE TABLE IF NOT EXISTS well_deleted (
  well_id    TEXT PRIMARY KEY,
  deleted_at TEXT NOT NULL,
  deleted_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_session (
  token_hash TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES app_user(user_id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team (
  team_id  TEXT PRIMARY KEY,
  team_no  TEXT NOT NULL UNIQUE,
  name     TEXT NOT NULL,
  rig_type TEXT,
  unit     TEXT,
  captain  TEXT
);

CREATE TABLE IF NOT EXISTS well (
  well_id         TEXT PRIMARY KEY,   -- 单井井号是唯一身份识别码
  market          TEXT NOT NULL,
  block           TEXT NOT NULL,
  well_category   TEXT NOT NULL,
  well_type       TEXT NOT NULL,
  spud_sections   INTEGER NOT NULL DEFAULT 2,
  design_depth    REAL NOT NULL,
  actual_depth    REAL NOT NULL DEFAULT 0,
  client          TEXT,
  contract_amount REAL NOT NULL DEFAULT 0,  -- 合同收入（总表 O 列）
  status          TEXT NOT NULL CHECK (status IN ('计划', '正钻', '完钻', '完井', '已结算'))
);

-- 哪支队伍在哪口井上干了哪段时间、处于哪个开次；规则条件与日报表头都从这里取值
CREATE TABLE IF NOT EXISTS team_well_period (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id  TEXT NOT NULL REFERENCES team(team_id),
  well_id  TEXT NOT NULL REFERENCES well(well_id),
  phase    TEXT NOT NULL,             -- 一开 / 二开 / 三开 ...
  start_at TEXT NOT NULL,             -- YYYY-MM-DD
  end_at   TEXT                       -- 为空表示至今
);

CREATE TABLE IF NOT EXISTS cost_item (
  item_code     TEXT PRIMARY KEY,     -- C1 / C2-1-02-01 / R02 ...
  item_name     TEXT NOT NULL,
  parent_code   TEXT REFERENCES cost_item(item_code),
  level         INTEGER NOT NULL,
  is_leaf       INTEGER NOT NULL,
  direction     TEXT NOT NULL CHECK (direction IN ('收入', '成本')),
  cost_behavior TEXT CHECK (cost_behavior IN ('固定', '变动')),
  calc_type     TEXT,                 -- 计价类型 A–G
  owner_dept    TEXT,                 -- 归口部室
  report_col    TEXT,                 -- 《全要素经营总表》列号
  sort_no       INTEGER NOT NULL,
  finance_account TEXT
);

CREATE TABLE IF NOT EXISTS material (
  material_code TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  unit          TEXT NOT NULL,
  item_code     TEXT REFERENCES cost_item(item_code),
  enabled       INTEGER NOT NULL DEFAULT 1   -- 停用后不再选用，历史引用保留
);

CREATE TABLE IF NOT EXISTS unit_of_measure (
  unit  TEXT PRIMARY KEY,
  usage TEXT NOT NULL
);

-- 单井基础信息填报：基层队按井登记、随施工进度更新；字段与《单井基础信息表》一致。
-- 一井一队当前只有一条“已提交”记录，更新时生成新版本，旧版本标为“已被更新”。
CREATE TABLE IF NOT EXISTS well_info_report (
  report_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  well_id       TEXT NOT NULL,
  team_id       TEXT NOT NULL REFERENCES team(team_id),
  version       INTEGER NOT NULL DEFAULT 1,
  status        TEXT NOT NULL CHECK (status IN ('草稿', '已提交', '已被更新')),
  period        TEXT,                 -- 归属年月 YYYY-MM（表中“年份 + 月份”）
  market        TEXT, captain TEXT, rig_type TEXT, client TEXT, block TEXT,
  well_category TEXT, well_type TEXT,
  spud_sections INTEGER,              -- 开次
  design_depth  REAL,
  milestones    TEXT NOT NULL DEFAULT '{}',   -- JSON：搬安 / 砸导管 / 开钻k / 完钻k / 完钻 / 完井 → YYYY-MM-DD HH:MM
  staff         TEXT NOT NULL DEFAULT '{}',   -- JSON：合同化 / 市场化 / 第三方 → 人数
  creator       TEXT,
  created_at    TEXT, updated_at TEXT, submitted_at TEXT,
  registered_at TEXT,                 -- 首次提交时间（表中“填报时间”），用于登记滞后
  completion_reported_at TEXT,        -- 完井时间首次填报时间
  supersedes_report_id INTEGER REFERENCES well_info_report(report_id),
  source        TEXT NOT NULL DEFAULT '填报',   -- 填报 / 导入
  return_comment TEXT,                -- 企划财务部退回队伍修改的原因（或导入校验未通过的问题）
  returned_by   TEXT,
  returned_at   TEXT
);

-- 井施工节点：一个节点一行，开次再多也不用加列；由基础信息提交生成
CREATE TABLE IF NOT EXISTS well_milestone (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  well_id        TEXT NOT NULL REFERENCES well(well_id),
  team_id        TEXT NOT NULL REFERENCES team(team_id),
  milestone      TEXT NOT NULL,       -- 搬安 / 砸导管 / 开钻 / 完钻 / 全井完钻 / 完井
  section_no     INTEGER,             -- 开次（仅开钻、完钻）
  occurred_at    TEXT NOT NULL,
  scenario       TEXT NOT NULL DEFAULT '实际',
  info_report_id INTEGER REFERENCES well_info_report(report_id)
);

-- ---------- 二、依据与规则 ----------

CREATE TABLE IF NOT EXISTS source_doc (
  doc_id            INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_type          TEXT NOT NULL,
  doc_no            TEXT,
  doc_name          TEXT NOT NULL,
  counterparty      TEXT,
  counterparty_type TEXT,
  file_hash         TEXT,
  parse_status      TEXT NOT NULL DEFAULT '已解析'
);

CREATE TABLE IF NOT EXISTS clause (
  clause_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id        INTEGER NOT NULL REFERENCES source_doc(doc_id),
  clause_no     TEXT NOT NULL,
  page_no       TEXT,
  text_pre      TEXT NOT NULL DEFAULT '',
  text_hit      TEXT NOT NULL,        -- 高亮部分：AI 提取依据
  text_post     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS economic_rule (
  rule_id         INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_code       TEXT NOT NULL,      -- 跨版本不变
  version         INTEGER NOT NULL,
  name            TEXT NOT NULL,
  item_code       TEXT NOT NULL REFERENCES cost_item(item_code),
  clause_id       INTEGER REFERENCES clause(clause_id),
  fact_type       TEXT,               -- 进尺 / 日历天 / 人员出勤 / 材料消耗 / 外包服务 ...；比例类为空
  pricing_method  TEXT NOT NULL,      -- 单价 / 包干 / 比例 / 定额日费
  base_item_code  TEXT,               -- 比例类的基数项目
  calc_order      INTEGER NOT NULL,   -- 1 收入 → 2 直接成本 → 3 比例计提
  unit_price      REAL NOT NULL,
  unit            TEXT,
  calc_params     TEXT NOT NULL DEFAULT '{}',   -- JSON：系数条件等
  combine_mode    TEXT NOT NULL DEFAULT '互斥',  -- 互斥 / 叠加
  granularity     TEXT NOT NULL DEFAULT '逐日',
  priority        INTEGER NOT NULL DEFAULT 0,
  valid_from      TEXT NOT NULL,
  valid_to        TEXT,
  status          TEXT NOT NULL CHECK (status IN ('草稿', '待确认', '回算中', '待发布', '已发布', '已被替代', '已废止')),
  origin          TEXT NOT NULL,      -- AI提取 / 人工录入 / 历史反算
  ai_confidence   REAL,
  field_levels    TEXT NOT NULL DEFAULT '{}',   -- JSON：各字段 AI 置信等级 高/中/低
  supersedes_rule_id INTEGER REFERENCES economic_rule(rule_id),
  confirmed_by    TEXT,
  confirmed_at    TEXT,
  published_at    TEXT,
  UNIQUE (rule_code, version)
);

CREATE TABLE IF NOT EXISTS rule_condition (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id   INTEGER NOT NULL REFERENCES economic_rule(rule_id),
  dimension TEXT NOT NULL,            -- 市场 / 区块 / 井别 / 井型 / 开次 / 队伍 / 钻机型号 / 井深
  operator  TEXT NOT NULL,            -- 等于 / 属于 / 大于
  value     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_test (
  test_id         INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id         INTEGER NOT NULL REFERENCES economic_rule(rule_id),
  well_id         TEXT REFERENCES well(well_id),
  period          TEXT,
  expected_amount REAL,
  calc_amount     REAL,
  deviation_rate  REAL,
  result          TEXT NOT NULL,      -- 通过 / 不通过 / 豁免
  tested_at       TEXT NOT NULL
);

-- AI 调用记录：每次解析一条，含文件指纹、模型与提示词版本、原始输出与结果统计；采纳情况从规则状态与修改记录统计
CREATE TABLE IF NOT EXISTS ai_task (
  task_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  task_type      TEXT NOT NULL,          -- 规则解析
  doc_id         INTEGER REFERENCES source_doc(doc_id),
  status         TEXT NOT NULL CHECK (status IN ('进行中', '完成', '失败')),
  model          TEXT,
  prompt_version TEXT NOT NULL,
  input_hash     TEXT,
  input_chars    INTEGER,
  truncated      INTEGER NOT NULL DEFAULT 0,
  output         TEXT,                   -- 模型原始输出（留档，可复核）
  draft_count    INTEGER NOT NULL DEFAULT 0,
  skipped        TEXT,                   -- JSON：不采纳的规则及原因
  manual         TEXT,                   -- JSON：写不成公式、需人工计价的条款
  error          TEXT,
  created_by     TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  finished_at    TEXT
);

-- ---------- 三、事实 ----------

-- 成本日报报量项目字典：只收录需要基层采集、参与成本计算的量；以末级费用项目编码为键
CREATE TABLE IF NOT EXISTS report_line (
  line_key   TEXT PRIMARY KEY,        -- 末级费用项目编码
  section    TEXT NOT NULL,           -- 段：staff / energy / equip / service / logistics
  section_no INTEGER NOT NULL,
  group_name TEXT NOT NULL,           -- 段内分组，如 提速服务
  label      TEXT NOT NULL,
  unit       TEXT NOT NULL,
  fact_type  TEXT NOT NULL,           -- 生成的业务事实类型
  item_code  TEXT NOT NULL,
  needs_doc  INTEGER NOT NULL DEFAULT 0,   -- 须填签证单 / 单据号
  core       INTEGER NOT NULL DEFAULT 0,   -- 默认显示
  enabled    INTEGER NOT NULL DEFAULT 1,
  sort_no    INTEGER NOT NULL
);

-- 成本日报：一井一队一天一张，只报量、不含金额
CREATE TABLE IF NOT EXISTS daily_report (
  report_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  well_id      TEXT NOT NULL REFERENCES well(well_id),
  team_id      TEXT NOT NULL REFERENCES team(team_id),
  report_date  TEXT NOT NULL,
  version      INTEGER NOT NULL DEFAULT 1,
  status       TEXT NOT NULL CHECK (status IN ('草稿', '已提交', '已审核', '驳回', '已被更正')),
  phase        TEXT,
  depth_start  REAL,
  depth_end    REAL,
  hours        TEXT NOT NULL DEFAULT '{}',   -- JSON：工况 → 小时
  lines        TEXT NOT NULL DEFAULT '{}',   -- JSON：报量项目编码 → 数量
  line_notes   TEXT NOT NULL DEFAULT '{}',   -- JSON：报量项目编码 → 签证单号 / 设备编号
  reporter     TEXT,
  submitted_at TEXT,
  reviewer     TEXT,
  reviewed_at  TEXT,
  review_comment TEXT,
  supersedes_report_id INTEGER REFERENCES daily_report(report_id)
);

CREATE TABLE IF NOT EXISTS business_fact (
  fact_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id   INTEGER REFERENCES daily_report(report_id),
  scenario    TEXT NOT NULL DEFAULT '实际',
  fact_date   TEXT NOT NULL,
  team_id     TEXT NOT NULL REFERENCES team(team_id),
  well_id     TEXT NOT NULL REFERENCES well(well_id),
  fact_type   TEXT NOT NULL,
  quantity    REAL NOT NULL,
  unit        TEXT NOT NULL,
  attributes  TEXT NOT NULL DEFAULT '{}',
  source      TEXT NOT NULL,          -- 基层填报 / 系统接口 / 基础信息 / 单据识别
  status      TEXT NOT NULL CHECK (status IN ('已提交', '已审核', '已冲销'))
);

CREATE TABLE IF NOT EXISTS validation_alert (
  alert_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id  INTEGER NOT NULL REFERENCES daily_report(report_id),
  check_rule TEXT NOT NULL,
  level      TEXT NOT NULL CHECK (level IN ('提示', '警告', '阻断')),
  message    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  object_type TEXT NOT NULL,          -- 日报 / 规则 / 计算批次
  object_id   TEXT NOT NULL,
  action      TEXT NOT NULL,
  operator    TEXT NOT NULL,
  operated_at TEXT NOT NULL,
  comment     TEXT
);

-- ---------- 四、计算层 ----------

-- 费用池：企划财务部录入上年发生总额与上年实际钻机台月，本年度按元/台月分到队、到井；分摊结果以“分摊”类事项写入经济事项
CREATE TABLE IF NOT EXISTS cost_pool (
  pool_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  period        TEXT NOT NULL,        -- YYYY-MM（队天、进尺）或 YYYY（台月，年度池）
  item_code     TEXT NOT NULL REFERENCES cost_item(item_code),
  scope_team_id TEXT REFERENCES team(team_id),   -- 历史月度池的队伍范围，新池不再使用
  scope_market  TEXT,                 -- 范围：大港 / 长庆 / 西北，为空表示全部
  prev_rig_months REAL,               -- 上年实际钻机台月；分摊标准 = amount（上年发生总额）÷ 本字段
  basis         TEXT NOT NULL CHECK (basis IN ('队天', '进尺', '台月')),
  amount        REAL NOT NULL,
  note          TEXT,
  status        TEXT NOT NULL CHECK (status IN ('有效', '已作废')),
  created_by    TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  voided_by     TEXT,
  voided_at     TEXT
);


CREATE TABLE IF NOT EXISTS calc_batch (
  batch_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger        TEXT NOT NULL,       -- 初始化 / 事实审核通过 / 规则新版本发布 / 手工重算
  scope          TEXT NOT NULL,       -- JSON
  engine_version TEXT NOT NULL,
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  event_count    INTEGER NOT NULL DEFAULT 0,
  amount_change  REAL NOT NULL DEFAULT 0,
  operator       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS economic_event (
  event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  scenario       TEXT NOT NULL DEFAULT '实际',
  event_date     TEXT NOT NULL,
  period         TEXT NOT NULL,       -- YYYY-MM
  well_id        TEXT NOT NULL REFERENCES well(well_id),
  team_id        TEXT REFERENCES team(team_id),
  item_code      TEXT NOT NULL REFERENCES cost_item(item_code),
  direction      TEXT NOT NULL,
  cost_behavior  TEXT,
  owner_dept     TEXT,
  origin_type    TEXT NOT NULL,       -- 规则计算 / 比例计提 / 历史导入 / 人工计价
  fact_id        INTEGER REFERENCES business_fact(fact_id),
  base_event_ids TEXT,
  rule_id        INTEGER REFERENCES economic_rule(rule_id),
  quantity       REAL,
  unit_price     REAL,
  factor         REAL,
  amount         REAL NOT NULL,       -- 冲销记录为负数
  calc_snapshot  TEXT NOT NULL DEFAULT '{}',
  batch_id       INTEGER REFERENCES calc_batch(batch_id),
  status         TEXT NOT NULL CHECK (status IN ('有效', '已冲销', '冲销')),
  reversal_of    INTEGER REFERENCES economic_event(event_id),
  source_note    TEXT                 -- 历史导入的来源说明
);

CREATE INDEX IF NOT EXISTS ix_event_well_period ON economic_event (well_id, period);
CREATE INDEX IF NOT EXISTS ix_event_item ON economic_event (item_code);
CREATE INDEX IF NOT EXISTS ix_fact_well_date ON business_fact (well_id, fact_date);

-- 事项只冲不改：数据库层面禁止修改金额和删除事项
CREATE TRIGGER IF NOT EXISTS trg_event_no_amount_update BEFORE UPDATE OF amount, quantity, unit_price, item_code, well_id ON economic_event
BEGIN SELECT RAISE(ABORT, '经济事项只冲不改'); END;
CREATE TRIGGER IF NOT EXISTS trg_event_no_delete BEFORE DELETE ON economic_event
BEGIN SELECT RAISE(ABORT, '经济事项只冲不改'); END;
-- 规则只增不改：已发布规则的价格与条件不能改
CREATE TRIGGER IF NOT EXISTS trg_rule_published_frozen BEFORE UPDATE OF unit_price, calc_params, item_code, fact_type, valid_from ON economic_rule
WHEN OLD.status IN ('已发布', '已被替代', '已废止')
BEGIN SELECT RAISE(ABORT, '已发布规则只增不改'); END;
