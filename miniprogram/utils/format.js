function round(value) { return Math.round(Number(value || 0)); }
function dateLabel(value) { if (!value) return ''; const date = new Date(value); return `${date.getMonth() + 1}月${date.getDate()}日`; }
function mealLabel(value) { return ({ breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐' })[value] || value || '餐次'; }
module.exports = { round, dateLabel, mealLabel };
