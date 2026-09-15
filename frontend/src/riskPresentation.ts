import type { SafetyRisk } from "./api.ts";

export type RiskPresentationStage = "proposal" | "recorded" | "recommendation";

function riskDetail(message: string): string {
  return message.replace(/^(高风险记录|已阻止|请留意)[：:]\s*/, "").replace(/[。；;]+$/, "");
}

export function presentSafetyRisk(risk: SafetyRisk, stage: RiskPresentationStage): string {
  const detail = riskDetail(risk.message);
  if (risk.evaluation_mode === "record" && risk.decision_effect === "recorded_with_warning") {
    if (stage === "proposal") {
      return `这是已发生事实的待确认记录；确认后保存并标注风险。${detail}。未来推荐仍会拦截相关食物。`;
    }
    return `已记录高风险饮食。${detail}。未来推荐仍会拦截相关食物。`;
  }
  if (risk.evaluation_mode === "recommendation" && risk.decision_effect === "blocked") {
    return `已阻止：${detail}${risk.alternative ? `。${risk.alternative}` : ""}`;
  }
  return `请留意：${detail}`;
}

export function primaryRecordedWarning(risks: readonly SafetyRisk[]): SafetyRisk | undefined {
  return risks.find((risk) => risk.evaluation_mode === "record" && risk.decision_effect === "recorded_with_warning");
}
