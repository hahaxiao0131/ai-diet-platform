# 数据库设计

**适用版本：V1.1 / P0.1**  
**数据库：PostgreSQL**  
**主键：UUID**  
**时间：UTC存储，接口统一返回ISO 8601**

本地V1预览使用仓储适配器将业务快照原子写入`backend/data/local_store.json`，并保留上一版备份。该文件仅用于解决本地重启后的数据丢失，不改变PostgreSQL作为生产数据库的约束。

## 1. 通用约定

所有核心业务表包含：

```text
id
created_at
updated_at
deleted_at
```

所有业务数据必须关联：

```text
user_id
profile_id
```

算法或AI生成的数据必须保存：

```text
rule_version
nutrition_version
model_provider
model_name
prompt_version
data_source
confidence
```

金额、重量和营养数值统一使用：

* 重量：`numeric(10,2)`，单位g；
* 能量：`numeric(10,2)`，单位kcal；
* 营养素：`numeric(10,2)`，单位g或mg；
* 比例：`numeric(5,4)`，范围0～1。

## 2. 核心表

### 2.1 users

```text
id uuid pk
status varchar              -- active/deleted/blocked
created_at timestamptz
updated_at timestamptz
deleted_at timestamptz null
```

### 2.2 user_identities

```text
id uuid pk
user_id uuid fk users.id
provider varchar            -- mock/wechat/phone/apple
provider_subject varchar
created_at timestamptz
unique(provider, provider_subject)
```

业务代码不得直接把微信openid作为业务主键。

### 2.3 profiles

```text
id uuid pk
user_id uuid fk users.id
sex varchar                 -- male/female/unknown
birth_year smallint
height_cm numeric(6,2)
current_weight_kg numeric(6,2)
activity_level varchar
primary_goal varchar        -- fat_loss/muscle_gain/maintain/structure
target_weight_kg numeric(6,2) null
goal_pace varchar null      -- steady/standard/aggressive
onboarding_completed_at timestamptz null
```

V1一个用户只允许一个profile，但所有业务表必须使用`profile_id`，为未来多档案预留。

### 2.4 goal_versions

```text
id uuid pk
profile_id uuid fk profiles.id
primary_goal varchar
pace varchar
target_weight_kg numeric(6,2) null
target_calories_kcal numeric(10,2)
target_protein_g numeric(10,2)
target_fat_g numeric(10,2)
target_carbs_g numeric(10,2)
target_fiber_g numeric(10,2)
target_water_ml integer
calculation_inputs jsonb
rule_version varchar
status varchar              -- proposed/active/superseded
effective_from timestamptz
effective_to timestamptz null
confirmed_at timestamptz null
```

同一个profile同时只能有一个`active`目标版本。

### 2.5 foods

```text
id uuid pk
name varchar
aliases jsonb
food_type varchar           -- ingredient/standard_dish/packaged/custom
default_unit varchar
default_weight_g numeric(10,2) null
source varchar
source_version varchar null
nutrition_per_100g jsonb
allergens jsonb
tags jsonb
verified_at timestamptz null
confidence varchar
status varchar              -- active/disabled
```

`nutrition_per_100g`至少包含：

```json
{
  "energy_kcal": 0,
  "protein_g": 0,
  "fat_g": 0,
  "carbs_g": 0,
  "fiber_g": 0,
  "sodium_mg": 0,
  "added_sugar_g": null,
  "vegetable_g": 0,
  "fruit_g": 0
}
```

### 2.6 recipes

```text
id uuid pk
profile_id uuid fk profiles.id null
name varchar
recipe_type varchar        -- standard/user
servings numeric(6,2)
total_weight_g numeric(10,2)
nutrition_snapshot jsonb
source varchar
nutrition_version varchar
status varchar
```

### 2.7 recipe_items

```text
id uuid pk
recipe_id uuid fk recipes.id
food_id uuid fk foods.id
quantity_g numeric(10,2)
optional boolean
sort_order integer
```

### 2.8 meal_drafts

```text
id uuid pk
profile_id uuid fk profiles.id
meal_type varchar           -- breakfast/lunch/dinner/snack
draft_status varchar        -- processing/ready/confirmed/cancelled/expired
input_text text null
input_assets jsonb
vision_result jsonb
confidence varchar
model_provider varchar null
model_name varchar null
prompt_version varchar null
expires_at timestamptz null
```

草稿不参与当日正式营养汇总。

### 2.9 meal_draft_items

```text
id uuid pk
draft_id uuid fk meal_drafts.id
food_id uuid fk foods.id null
name varchar
estimated_weight_g numeric(10,2)
household_unit varchar
consumed_ratio numeric(5,4)
is_compound boolean
user_confirmed boolean
user_modified boolean
source varchar
confidence varchar
composition jsonb null
```

### 2.10 meals

```text
id uuid pk
profile_id uuid fk profiles.id
meal_type varchar
eaten_at timestamptz
record_source varchar       -- manual/photo/plan_copy/recipe
record_status varchar       -- active/deleted
source_draft_id uuid null
nutrition_snapshot jsonb
score_snapshot jsonb
confidence varchar
```

### 2.11 meal_items

```text
id uuid pk
meal_id uuid fk meals.id
food_id uuid fk foods.id null
recipe_id uuid fk recipes.id null
name varchar
weight_g numeric(10,2)
consumed_ratio numeric(5,4)
nutrition_snapshot jsonb
user_modified boolean
source varchar
```

正式记录保存营养快照，避免食物库更新后历史结果静默变化。

### 2.12 meal_plans

```text
id uuid pk
profile_id uuid fk profiles.id
planned_for_date date
meal_type varchar
plan_status varchar         -- draft/saved/converted/expired/cancelled
scenario varchar            -- home/quick/convenience
plan_payload jsonb
nutrition_preview jsonb
recommendation_reason text
model_provider varchar null
rule_version varchar
```

### 2.13 risk_alerts

```text
id uuid pk
profile_id uuid fk profiles.id
meal_id uuid null
alert_level varchar         -- notice/high/block
alert_type varchar
message text
matched_food_id uuid null
resolved boolean
resolved_at timestamptz null
```

### 2.14 weight_records

```text
id uuid pk
profile_id uuid fk profiles.id
weight_kg numeric(6,2)
measured_at timestamptz
fasting boolean null
note text null
```

## 3. 索引和约束

必须建立：

```text
meals(profile_id, eaten_at)
meal_drafts(profile_id, draft_status)
meal_plans(profile_id, planned_for_date)
weight_records(profile_id, measured_at)
goal_versions(profile_id, status)
foods(name)
```

业务约束：

* `consumed_ratio`必须在0～1；
* `weight_kg`、`height_cm`必须为正数；
* 已删除数据默认不出现在普通查询；
* 目标版本和营养快照不可原地覆盖；
* 正式meal必须至少包含一个meal_item。

## 4. P0不实现

* 多profile切换；
* 复杂会员权益；
* 支付订单；
* 设备同步；
* 库存和保质期；
* 完整微量营养素；
* 社区数据模型。
