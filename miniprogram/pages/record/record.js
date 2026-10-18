const api = require('../../utils/api');
Page({
  data: { mode: 'meal', foodName: '', foodWeight: '', food: null, mealTypes: ['早餐', '午餐', '晚餐', '加餐'], mealValues: ['breakfast', 'lunch', 'dinner', 'snack'], mealIndex: 1, exerciseType: '', duration: '', intensities: ['低强度', '中等', '高强度'], intensityValues: ['low', 'moderate', 'high'], intensityIndex: 1, saving: false, message: '', success: false },
  switchMode(event) { this.setData({ mode: event.currentTarget.dataset.mode, message: '' }); },
  onFoodName(event) { this.setData({ foodName: event.detail.value, food: null }); },
  onFoodWeight(event) { this.setData({ foodWeight: event.detail.value }); },
  onMealType(event) { this.setData({ mealIndex: Number(event.detail.value) }); },
  onExerciseType(event) { this.setData({ exerciseType: event.detail.value }); },
  onDuration(event) { this.setData({ duration: event.detail.value }); },
  onIntensity(event) { this.setData({ intensityIndex: Number(event.detail.value) }); },
  async saveMeal() {
    const name = this.data.foodName.trim(); const weight = Number(this.data.foodWeight);
    if (!name || !weight || weight <= 0) { this.setData({ message: '请填写食物名称和大于 0 的份量。', success: false }); return; }
    this.setData({ saving: true, message: '' });
    try {
      await getApp().ensureAuth();
      const foods = await api.searchFoods(name); const food = foods[0];
      if (!food) throw new Error('没有找到这个食物，请换个名称或使用 AI 记餐。');
      await api.createManualMeal({ meal_type: this.data.mealValues[this.data.mealIndex], items: [{ food_id: food.id, weight_g: weight, consumed_ratio: 1 }] });
      this.setData({ food, message: '饮食记录已保存。', success: true, foodName: '', foodWeight: '' });
    } catch (error) { this.setData({ message: error.message || '保存失败', success: false }); }
    finally { this.setData({ saving: false }); }
  },
  async saveExercise() {
    const type = this.data.exerciseType.trim(); const duration = Number(this.data.duration);
    if (!type || !duration || duration <= 0) { this.setData({ message: '请填写运动类型和大于 0 的时长。', success: false }); return; }
    this.setData({ saving: true, message: '' });
    try { await getApp().ensureAuth(); await api.createExercise({ exercise_type: type, duration_min: duration, intensity: this.data.intensityValues[this.data.intensityIndex] }); this.setData({ message: '运动记录已保存。', success: true, exerciseType: '', duration: '' }); }
    catch (error) { this.setData({ message: error.message || '保存失败', success: false }); }
    finally { this.setData({ saving: false }); }
  },
  goAssistant() { wx.switchTab({ url: '/pages/assistant/assistant' }); },
});
