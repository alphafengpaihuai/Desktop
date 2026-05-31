// 守一 CDSS 前端配置
// 用于桌面端联调，可被 bridge_server.py 动态覆盖

window.CLIENT_CONFIG = {
  // 默认数据库
  default_db: "汉方方案",
  
  // 数据库选项
  db_options: ["汉方方案", "清大方案"],
  
  // 品牌名称
  header_brand: "守一 · 中医AI辅助诊疗系统",
  
  // WebSocket 地址
  ws_url: "ws://localhost:8765",
  
  // HTTP API 地址
  api_base: "http://localhost:6005",
  
  // 桌面端标识
  platform: "desktop",
  
  // 版本号
  version: "1.0.0-bridge"
};

// 兼容旧版
window.CLINICAL_CONFIG = window.CLIENT_CONFIG;

console.log("[Config] server_config loaded:", window.CLIENT_CONFIG);
