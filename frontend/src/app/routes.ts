export const ROUTES = { chat: '/chat', tickets: '/tickets', knowledge: '/knowledge', settings: '/settings' } as const;
export function conversationPath(id: string) { return id ? `${ROUTES.chat}?session=${encodeURIComponent(id)}` : ROUTES.chat; }
