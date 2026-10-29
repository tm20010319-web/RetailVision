export const PrivacyMode = Object.freeze({
  FULL: "full",
  MASKED: "masked",
  EMOTIONAL_ONLY: "emotional_only",
});

export const memberDisplayData = Object.freeze({
  displayName: "李女士",
  memberTitle: "尊贵会员",
  totalSpend: 12860,
  points: 8520,
  visitCount: 31,
  companionDays: 728,
  privacyMode: PrivacyMode.FULL,
  emotionalCopy: "您的每一份支持，都值得被认真珍藏",
  serviceCopy: "专属服务已就绪，愿您今天依然收获愉悦与惊喜",
});

const tierTitles = {
  diamond: "钻石会员",
  platinum: "铂金会员",
  gold: "尊贵会员",
  silver: "优选会员",
  bronze: "会员",
};

export function toMemberDisplayData(member, fallback = memberDisplayData) {
  const joinDate = member?.join_date ? new Date(member.join_date) : null;
  const companionDays = joinDate && !Number.isNaN(joinDate.valueOf())
    ? Math.max(0, Math.floor((Date.now() - joinDate.valueOf()) / 86400000))
    : fallback.companionDays;
  return {
    ...fallback,
    displayName: member?.display_name || member?.name || fallback.displayName,
    memberTitle: tierTitles[member?.tier] || fallback.memberTitle,
    totalSpend: Number(member?.total_spent ?? fallback.totalSpend),
    points: Number(member?.points ?? fallback.points),
    visitCount: Number(member?.visit_count ?? fallback.visitCount),
    companionDays,
    memberId: member?.id,
  };
}

function currency(value) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

export function getMemberIdentity(data, privacyMode) {
  if (privacyMode === PrivacyMode.EMOTIONAL_ONLY) {
    return { greeting: "欢迎回来，尊贵会员", subtitle: "感谢您的长期信任" };
  }

  return {
    greeting: `欢迎回来，${data.displayName}`,
    subtitle: `${data.memberTitle} · 您的专属时刻`,
  };
}

export function getMemberMetrics(data, privacyMode) {
  if (privacyMode === PrivacyMode.EMOTIONAL_ONLY) {
    return [
      { label: "每一次选择", value: "一直被记得", caption: "感谢您的长期信任" },
      { label: "专属关系", value: "持续生长", caption: "陪伴从不只是数字" },
      { label: "相遇时刻", value: "值得期待", caption: "每次到来都有回应" },
      { label: "此刻体验", value: "已为您开启", caption: "愿今天依然愉悦" },
    ];
  }

  const masked = privacyMode === PrivacyMode.MASKED;
  return [
    {
      label: "累计消费",
      value: data.totalSpend,
      prefix: "¥",
      maskedValue: masked ? "¥12,***" : null,
      caption: "感谢您的每一次选择",
    },
    {
      label: "当前积分",
      value: data.points,
      suffix: "分",
      maskedValue: masked ? "8,***" : null,
      caption: "每一点信任都被认真记录",
    },
    {
      label: "到店次数",
      value: data.visitCount,
      suffix: "次",
      caption: `很高兴与您第 ${data.visitCount} 次相遇`,
    },
    {
      label: "陪伴时光",
      value: data.companionDays,
      suffix: "天",
      caption: "感谢一路相伴",
    },
  ].map((metric) => ({ ...metric, format: currency }));
}
