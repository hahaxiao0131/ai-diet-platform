# API设计

**适用版本：V1.1 / P0.1**  
**协议：REST JSON**  
**前缀：`/api/v1`**

## 1. 通用响应格式

成功：

```json
{
  "data": {},
  "request_id": "uuid"
}
```

失败：

```json
{
  "error": {
    "code": "MEAL_DRAFT_NOT_FOUND",
    "message": "饮食草稿不存在",
    "details": {}
  },
  "request_id": "uuid"
}
```

所有写入接口必须支持幂等请求头：

```text
Idempotency-Key: <uuid>
```

## 2. 认证和档案

### POST `/auth/mock-login`

请求：

```json
{
  "mock_user_key": "demo-user-001"
}
```

返回：

```json
{
  "access_token": "token",
  "user": {
    "id": "uuid",
    "is_new": true
  }
}
```

### GET `/profiles/me`

返回当前用户的profile、目标版本和约束摘要。

### PUT `/profiles/me`

修改档案。涉及目标的字段修改后不得直接生效，必须返回新的目标提案。

## 3. 目标

### POST `/goals/recalculate`

请求：

```json
{
  "profile_id": "uuid",
  "primary_goal": "fat_loss",
  "target_weight_kg": 65,
  "pace": "standard"
}
```

返回：

```json
{
  "proposal_id": "uuid",
  "old_goal": {},
  "new_goal": {
    "calories_kcal": 1800,
    "protein_g": 120,
    "fat_g": 60,
    "carbs_g": 210,
    "fiber_g": 25
  },
  "reasons": [],
  "rule_version": "energy-v1"
}
```

### POST `/goals/{proposal_id}/confirm`

确认并生成新的active目标版本。

### GET `/goals/current`

返回当前生效目标及目标版本信息。

## 4. 食物

### GET `/foods/search?q=鸡蛋`

支持名称、别名、分类标签和轻度错字近似搜索。结果按以下优先级排序：

```text
名称精确命中
> 别名精确命中
> 前缀命中
> 包含命中
> 分类命中
> 近似匹配
```

空查询只返回10项高频食材；有查询时最多返回16项最相关结果。食材目录数量不应直接决定页面展示数量。

本地扩展目录为V1检索兜底，营养值标记为中等可信度。后续接正式食物数据库时必须保留名称、别名、分类、默认份量和数据来源字段。

### GET `/foods/recent`

返回最近使用的食物和组合。

### POST `/foods/custom`

创建用户自定义食物。P0只支持手动输入名称、份量和营养标签。

### GET `/recipes`

返回系统标准菜谱和当前用户菜谱。

### POST `/recipes`

创建用户菜谱并计算营养快照。

## 5. 饮食草稿

### POST `/meal-drafts`

请求：

```json
{
  "meal_type": "lunch",
  "eaten_at": "2026-08-08T04:00:00Z",
  "assets": [
    {
      "type": "image",
      "storage_key": "local/demo.jpg"
    }
  ]
}
```

返回草稿ID和处理状态。

### GET `/meal-drafts/{draft_id}`

返回：

* 草稿状态；
* 识别食物；
* 估算份量；
* 置信度；
* 重复识别提示；
* 识别错误。

### PATCH `/meal-drafts/{draft_id}/items/{item_id}`

修改名称、份量、单位、实际食用比例或删除标记。

### POST `/meal-drafts/{draft_id}/items`

添加漏识别食物。

### POST `/meal-drafts/{draft_id}/confirm`

请求：

```json
{
  "items": [
    {
      "item_id": "uuid",
      "food_id": "uuid",
      "weight_g": 150,
      "consumed_ratio": 0.5
    }
  ]
}
```

后端必须在同一个业务事务中：

1. 校验食物和份量；
2. 计算营养；
3. 执行RiskEngine；
4. 执行ScoringEngine；
5. 创建正式meal；
6. 更新当日汇总。

## 6. 正式饮食记录

### GET `/meals?date=2026-08-08`

返回当天餐次、营养快照、评分、风险和记录完整度。

### POST `/meals/manual`

手动搜索后直接创建正式记录，仍然必须经过后端营养计算。

### PATCH `/meals/{meal_id}`

修改正式记录后，重新生成营养、评分、风险和当日汇总。

### DELETE `/meals/{meal_id}`

软删除并触发当日汇总重算。

### POST `/meals/{meal_id}/copy`

复制已有餐次到指定日期和餐次。

## 7. 今日首页

### GET `/today?date=2026-08-08`

返回：

```json
{
  "date": "2026-08-08",
  "status": "partial",
  "score": null,
  "score_status": "insufficient_data",
  "confidence": "medium",
  "completeness": {
    "recorded_meals": 1,
    "expected_meals": 3
  },
  "nutrition": {
    "consumed": {},
    "target": {},
    "remaining": {},
    "gaps": [],
    "near_limits": []
  },
  "meals": [],
  "next_meal": {}
}
```

## 8. 下一餐推荐

### POST `/meal-plans/preview`

请求：

```json
{
  "meal_type": "dinner",
  "scenario": "quick",
  "available_time_min": 10,
  "budget_level": "normal",
  "available_foods": [],
  "preferences": []
}
```

返回2～3套方案，每套包含：

* 菜品；
* 份量；
* 预计营养；
* 推荐理由；
* 风险；
* 替换选项；
* What-if后的全天结果。

### POST `/meal-plans/{plan_id}/replace`

替换食材后重新计算整餐。

### POST `/meal-plans/{plan_id}/save`

保存为计划餐。

### POST `/meal-plans/{plan_id}/convert`

吃完后转为正式meal，并允许修改实际食用量。

## 9. AI助手

### POST `/ai/actions/preview`

AI只返回结构化动作提案，不直接写库。

### POST `/ai/actions/{action_id}/confirm`

用户确认后执行动作。

### POST `/ai/actions/{action_id}/cancel`

取消动作。

### POST `/ai/chat`

用于解释和自然语言问答。涉及写入时必须转为AIAction。

V1只支持以下受控意图：

```text
explanation
meal_record_proposal
plan_recommendation
food_replacement
clarification
```

返回结构至少包含：

```json
{
  "kind": "meal_record_proposal",
  "message": "识别结果说明",
  "basis": ["回答依据"],
  "suggestions": ["可继续追问的问题"],
  "action": {
    "id": "uuid",
    "action_type": "create_meal",
    "status": "proposed",
    "payload": {},
    "preview_nutrition": {}
  },
  "cta": null
}
```

`meal_record_proposal`只生成待确认动作，不创建正式meal。营养预览、确认后营养快照、评分和风险均由后端规则模块计算。

## 10. 体重

### POST `/weights`

记录体重。

### GET `/weights/trend?days=30`

返回原始点和7日平滑趋势。

## 11. 错误码

至少包含：

```text
AUTH_REQUIRED
PROFILE_INCOMPLETE
GOAL_PROPOSAL_REQUIRED
MEAL_DRAFT_NOT_FOUND
MEAL_DRAFT_NOT_CONFIRMABLE
FOOD_NOT_FOUND
NUTRITION_CALCULATION_FAILED
RISK_BLOCKED
PLAN_NOT_FOUND
PLAN_ALREADY_CONVERTED
INVALID_CONSUMED_RATIO
INSUFFICIENT_DATA
```
