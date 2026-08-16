# 营养计算规则

**适用版本：V1.1 / P0.1**  
**默认规则版本：`nutrition-v1`**

## 1. 规则原则

* 所有核心营养由后端计算；
* AI不能直接决定营养值；
* 所有结果保存规则版本和数据来源；
* 估算结果不能伪装成精确测量；
* 用户确认值优先于AI估算值；
* 历史快照不得被新规则静默覆盖。

## 2. 营养字段

P0计算：

```text
energy_kcal
protein_g
fat_g
carbs_g
fiber_g
sodium_mg
added_sugar_g
vegetable_g
fruit_g
```

食物库基础营养默认按100g保存。

## 3. 食物份量计算

### 3.1 克数

```text
effective_weight_g = selected_weight_g × consumed_ratio
```

### 3.2 营养值

```text
nutrient_value =
nutrition_per_100g × effective_weight_g / 100
```

### 3.3 家用单位

P0至少支持：

```text
半碗
一碗
半盘
一盘
一拳
一掌
一个
一份
一勺
```

每个单位必须映射到可配置的默认克数。用户一旦输入精确克数，精确克数覆盖家用单位。

## 4. 复合菜

复合菜营养基于：

```text
标准菜谱配方
或
用户修正后的菜谱配方
```

用户默认只看到复合菜总量；点击“查看组成”后才展开食材。

烹调油、调味料属于可配置组成项。未知时使用估算区间和中等可信度，不得伪装为精确值。

## 5. 每日能量目标

### 5.1 基础代谢

P0默认使用Mifflin-St Jeor公式：

男性：

```text
BMR = 10 × weight_kg + 6.25 × height_cm - 5 × age + 5
```

女性：

```text
BMR = 10 × weight_kg + 6.25 × height_cm - 5 × age - 161
```

### 5.2 活动系数

活动系数配置如下：

```text
sedentary  = 1.20
light      = 1.375
moderate   = 1.55
high       = 1.725
```

```text
TDEE = BMR × activity_factor
```

### 5.3 目标调整

P0采用配置化调整，不允许前端自行计算：

```text
maintain   = TDEE
fat_loss   = TDEE × (1 - deficit_rate)
muscle_gain = TDEE × (1 + surplus_rate)
structure  = TDEE
```

`deficit_rate`和`surplus_rate`由目标节奏配置，必须设置上下限并经过产品及专业人员审核。

## 6. 三大营养素目标

P0先采用配置化比例和上下限：

```text
protein_g = target_calories × protein_ratio / 4
fat_g     = target_calories × fat_ratio / 9
carbs_g   = target_calories × carbs_ratio / 4
```

默认比例必须写入配置文件，不得散落在业务代码中。

目标生成后保存：

* 计算输入；
* 目标节奏；
* 最终目标值；
* 规则版本；
* 用户确认时间。

## 7. 每餐目标分配

P0默认：

```text
早餐 25%
午餐 35%
晚餐 30%
加餐 10%
```

餐次结构暂不支持复杂自定义。两餐制、夜班模式和训练后餐放到P1。

## 8. 当日汇总

```text
day_total = sum(all active meals on date)
remaining = target - day_total
```

对每个营养素分别判断：

* 不足；
* 合理；
* 接近上限；
* 偏高。

不能只根据剩余热量推荐下一餐。

## 9. 数据可信度

### 高

* 用户确认；
* 包装标签或高可信食物数据；
* 份量明确；
* 烹饪方式明确。

### 中

* AI识别后用户确认；
* 份量为家用单位；
* 烹调油或调味料部分未知。

### 低

* 图片不清晰；
* 食物类型不确定；
* 份量完全由模型估算；
* 复合菜组成未知。

可信度影响展示和是否允许生成最终评分，不直接作为扣分项。

## 10. 规则版本

规则发布格式：

```text
nutrition-v1
nutrition-v1.1
```

任何规则变更必须：

1. 新增版本；
2. 保留旧版本；
3. 增加回归测试；
4. 记录生效时间；
5. 不修改历史快照。
