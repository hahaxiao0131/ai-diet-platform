const API_BASE = "http://127.0.0.1:8000/api/v1";

let accessToken = window.localStorage.getItem("diet_access_token") ?? "";

export function setAccessToken(value: string) {
  accessToken = value;
  if (value) window.localStorage.setItem("diet_access_token", value);
  else window.localStorage.removeItem("diet_access_token");
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(options.headers ?? {}),
    },
  });
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message
        ?? (Array.isArray(detail) ? detail.map((item) => item?.msg).filter(Boolean).join("；") : "");
    throw new Error(message || "请求失败，请稍后重试");
  }
  return Object.prototype.hasOwnProperty.call(payload, "data") ? payload.data : payload;
}

export const api = {
  restoreSession: () => request<AuthSession>("/auth/session"),
  requestPhoneCode: (phone: string) => request<{ expires_in: number; message: string; dev_code?: string }>("/auth/phone/code", { method: "POST", body: JSON.stringify({ phone }) }),
  phoneLogin: (phone: string, code: string) => request<AuthSession>("/auth/phone/login", { method: "POST", body: JSON.stringify({ phone, code }) }),
  wechatLogin: (code: string) => request<AuthSession>("/auth/wechat/login", { method: "POST", body: JSON.stringify({ code }) }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  getProfile: () => request<Profile>("/profiles/me"),
  updateProfile: (payload: Partial<Profile>) =>
    request<Profile>("/profiles/me", { method: "PUT", body: JSON.stringify(payload) }),
  recalculateGoal: () => request<GoalProposal>("/goals/recalculate", { method: "POST" }),
  confirmGoal: (id: string) => request<GoalProposal>(`/goals/${id}/confirm`, { method: "POST" }),
  getGoal: () => request<GoalProposal | null>("/goals/current"),
  getToday: () => request<TodaySummary>("/today"),
  searchFoods: (query: string) => request<Food[]>(`/foods/search?q=${encodeURIComponent(query)}`),
  getRecentFoods: () => request<Food[]>("/foods/recent"),
  createCustomFood: (payload: CustomFoodCreatePayload) =>
    request<Food>("/foods/custom", { method: "POST", body: JSON.stringify(payload) }),
  lookupBarcode: (barcode: string) => request<Food>(`/foods/barcode/${encodeURIComponent(barcode)}`),
  createLabelFood: (payload: PackagedFoodLabelPayload) =>
    request<Food>("/foods/label", { method: "POST", body: JSON.stringify(payload) }),
  createManualMeal: (payload: ManualMealCreatePayload) =>
    request<Meal>("/meals/manual", { method: "POST", body: JSON.stringify(payload) }),
  createDraft: (mealType: string) =>
    request<MealDraft>("/meal-drafts", {
      method: "POST",
      body: JSON.stringify({
        meal_type: mealType,
        assets: [{ type: "image", storage_key: "mock-camera-input.jpg" }],
      }),
    }),
  confirmDraft: (draft: MealDraft, items: MealDraftItem[] = draft.items) =>
    request<Meal>("/meal-drafts/" + draft.id + "/confirm", {
      method: "POST",
      body: JSON.stringify({
        items: items.map((item) => ({
          item_id: item.id,
          food_id: item.food_id,
          name: item.name,
          weight_g: item.estimated_weight_g,
          consumed_ratio: item.consumed_ratio,
        })),
      }),
    }),
  previewPlans: () =>
    request<{ plans: MealPlan[]; remaining_before: Nutrition }>("/meal-plans/preview", {
      method: "POST",
      body: JSON.stringify({ meal_type: "dinner", scenario: "quick" }),
    }),
  savePlan: (id: string) => request<MealPlan>(`/meal-plans/${id}/save`, { method: "POST" }),
  replacePlan: (id: string, index: number, foodName: string) =>
    request<MealPlan>(`/meal-plans/${id}/replace`, {
      method: "POST",
      body: JSON.stringify({ index, food_name: foodName }),
    }),
  chat: (message: string) =>
    request<AIChatResponse>("/ai/chat", { method: "POST", body: JSON.stringify({ message }) }),
  getAiContext: () => request<AgentContext>("/ai/context"),
  getAiMemories: () => request<AgentMemory[]>("/ai/memories"),
  deleteAiMemory: (id: string) => request<AgentMemory>(`/ai/memories/${id}`, { method: "DELETE" }),
  giveAiFeedback: (traceId: string, rating: "helpful" | "not_helpful") =>
    request<AgentFeedback>(`/ai/traces/${traceId}/feedback`, { method: "POST", body: JSON.stringify({ rating }) }),
  confirmAiAction: (id: string) =>
    request<{ action: AIAction; meal: Meal | null; memory: AgentMemory | null }>(`/ai/actions/${id}/confirm`, { method: "POST" }),
  cancelAiAction: (id: string) =>
    request<AIAction>(`/ai/actions/${id}/cancel`, { method: "POST" }),
};

export type AuthSession = {
  access_token?: string;
  user: { id: string; is_new: boolean };
};

export type Profile = {
  id: string;
  user_id: string;
  sex: "male" | "female" | "unknown" | null;
  age: number | null;
  height_cm: number | null;
  current_weight_kg: number | null;
  activity_level: "sedentary" | "light" | "moderate" | "high" | null;
  primary_goal: "fat_loss" | "muscle_gain" | "maintain" | "structure" | null;
  goal_pace: "steady" | "standard" | "aggressive" | null;
  onboarding_completed: boolean;
};

export type Nutrition = {
  energy_kcal: number;
  protein_g: number;
  fat_g: number;
  carbs_g: number;
  fiber_g: number;
  sodium_mg: number;
  sugars_g: number | null;
  added_sugar_g: number | null;
  vegetable_g: number;
  fruit_g: number;
};

export type Food = {
  id: string;
  name: string;
  aliases: string[];
  food_type: "ingredient" | "standard_dish" | "packaged" | "custom";
  default_unit: string;
  default_weight_g: number | null;
  nutrition_per_100g: Nutrition;
  confidence: string;
  source: string;
  source_version: string;
  source_url: string | null;
  source_observed_at: string | null;
  barcode: string | null;
  brand: string | null;
  verified_by_user: boolean;
};

export type CustomFoodCreatePayload = {
  name: string;
  basis_weight_g: number;
  default_weight_g?: number;
  nutrition: Nutrition;
};

export type PackagedFoodLabelPayload = CustomFoodCreatePayload & {
  barcode?: string;
  brand?: string;
};

export type ManualMealCreatePayload = {
  meal_type: "breakfast" | "lunch" | "dinner" | "snack";
  items: {
    food_id: string;
    weight_g: number;
    consumed_ratio: number;
  }[];
};

export type GoalProposal = {
  id: string;
  target: Nutrition;
  reasons: string[];
  goal_type: string;
  pace: string;
  status: string;
};

export type MealDraftItem = {
  id: string;
  food_id: string;
  name: string;
  estimated_weight_g: number;
  household_unit: string;
  consumed_ratio: number;
  confidence: string;
};

export type MealDraft = {
  id: string;
  meal_type: MealType;
  items: MealDraftItem[];
  confidence: string;
};

export type Meal = {
  id: string;
  meal_type: string;
  items: { food_id: string | null; name: string; weight_g: number; consumed_ratio: number; nutrition: Nutrition }[];
  nutrition: Nutrition;
  score: { score: number; negative_points: string[]; next_actions: string[] };
  risks: { level: string; message: string }[];
  confidence: string;
};

export type MealPlan = {
  id: string;
  title: string;
  scenario: string;
  items: { food_id: string | null; name: string; weight_g: number; nutrition: Nutrition }[];
  nutrition: Nutrition;
  reason: string;
  risks: { level: string; message: string }[];
  status: "draft" | "saved" | "converted" | "cancelled";
};

export type MealType = "breakfast" | "lunch" | "dinner" | "snack";

export type TodaySummary = {
  date: string;
  status: "empty" | "partial" | "complete";
  score: number | null;
  score_status: string;
  confidence: string;
  completeness: { recorded_meals: number; expected_meals: number };
  consumed: Nutrition;
  target: Nutrition;
  remaining: Nutrition;
  gaps: string[];
  near_limits: string[];
  meals: Meal[];
};

export type AIAction = {
  id: string;
  action_type: "create_meal" | "remember_preference";
  title: string;
  summary: string;
  payload: {
    meal_type?: "breakfast" | "lunch" | "dinner" | "snack";
    items?: { food_id: string; name: string; weight_g: number; consumed_ratio: number }[];
    meal_id?: string;
    memory_id?: string;
    category?: "preference" | "avoidance" | "habit";
    value?: string;
  };
  preview_nutrition: Nutrition;
  confidence: AgentConfidence;
  assumptions: string[];
  source_trace_id: string | null;
  status: "proposed" | "confirmed" | "cancelled";
};

export type AgentConfidence = "high" | "medium" | "low";

export type AgentToolCallAudit = {
  name: string;
  effect: "read_only" | "proposal" | "confirmed_write";
  arguments: Record<string, unknown>;
  success: boolean;
  result_summary: string | null;
  error_code: string | null;
  cached: boolean;
};

export type AgentContext = {
  recorded_meals: number;
  expected_meals: number;
  remaining_energy_kcal: number;
  remaining_protein_g: number;
  remaining_fiber_g: number;
  gaps: string[];
  near_limits: string[];
  data_confidence: AgentConfidence;
  missing_data: string[];
  active_memories: string[];
};

export type AgentMemory = {
  id: string;
  category: "preference" | "avoidance" | "habit";
  value: string;
  status: "active" | "deleted";
  created_at: string;
};

export type AgentFeedback = {
  trace_id: string;
  rating: "helpful" | "not_helpful";
};

export type AIChatResponse = {
  kind: "explanation" | "meal_record_proposal" | "consumption_advice" | "plan_recommendation" | "food_replacement" | "food_nutrition" | "dietary_knowledge" | "memory_proposal" | "clarification" | "safety";
  message: string;
  basis: string[];
  suggestions: string[];
  action: AIAction | null;
  cta: "preview_plans" | null;
  trace_id: string | null;
  confidence: AgentConfidence;
  decision_stage: "inform" | "clarify" | "propose" | "safety";
  context: AgentContext | null;
  needs_clarification: boolean;
  clarification_options: string[];
  tool_calls: AgentToolCallAudit[];
  provider: string;
  model: string | null;
  latency_ms: number | null;
  fallback_used: boolean;
  fallback_reason: string | null;
  intent_conflict: string | null;
};
