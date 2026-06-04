from __future__ import annotations

import json


def capability_admin_page(default_user: str = "root") -> str:
    default_user_json = json.dumps(default_user, ensure_ascii=False)
    return """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>能力治理控制台 · Agent Capability Hub</title>
    <link rel="stylesheet" href="/static/capabilities/app.css">
  </head>
  <body>
    <div class="layout">
      <aside class="sidebar" aria-label="能力治理导航">
        <div class="sidebar-header">
          <div class="sidebar-logo">
            <div class="sidebar-logo-icon">A</div>
            <div class="sidebar-logo-text">能力治理控制台<span>Agent Capability Hub</span></div>
          </div>
        </div>
        <nav class="sidebar-nav">
          <div class="nav-group-label">能力管理</div>
          <button class="nav-item active" data-view="catalog" type="button">能力目录</button>
          <button class="nav-item" data-view="services" type="button">MCP 服务</button>
          <button class="nav-item" data-view="tools" type="button">工具清单</button>
          <div class="nav-group-label">治理策略</div>
          <button class="nav-item" data-view="profiles" type="button">Project Profile</button>
          <div class="nav-group-label">调用观测</div>
          <button class="nav-item" data-view="logs" type="button">调用日志</button>
          <div class="nav-group-label">接入配置</div>
          <button class="nav-item" data-view="claude" type="button">Claude Code 接入</button>
        </nav>
        <div class="sidebar-footer">
          <span class="status-dot"></span>
          阶段 1.5 治理视图
        </div>
      </aside>

      <main class="main">
        <header class="topbar">
          <div>
            <p class="eyebrow">Agent Capability Hub</p>
            <h1>能力治理控制台</h1>
          </div>
        </header>

        <section class="message-area" id="messageArea" role="status" aria-live="polite"></section>

        <section class="view active" id="view-catalog">
          <div class="panel">
            <div class="panel-heading">
              <div>
                <h2>能力目录</h2>
                <p>按 Project Profile 预览当前可见的能力来源。</p>
              </div>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>来源</th>
                    <th>描述</th>
                    <th>状态</th>
                    <th>标签</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody id="catalogTable">
                  <tr><td colspan="5" class="empty">正在加载能力目录...</td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section class="view" id="view-services">
          <div class="content-grid">
            <section class="panel services-panel">
              <div class="panel-heading">
                <div>
                  <h2>MCP 服务</h2>
                  <p>登记内部 MCP 端点并同步其工具定义。</p>
                </div>
                <button class="primary" id="openServiceDialog" type="button">登记服务</button>
              </div>
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>服务</th>
                      <th>端点</th>
                      <th>状态</th>
                      <th>标签</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody id="servicesTable">
                  <tr><td colspan="5" class="empty">正在读取服务...</td></tr>
                  </tbody>
                </table>
              </div>
            </section>

          </div>
        </section>

        <section class="view" id="view-tools">
          <section class="panel tools-panel">
            <div class="panel-heading">
              <div>
                <h2>工具清单</h2>
                <p id="selectedServiceHint">按服务和层级筛选已同步工具。</p>
              </div>
            </div>
            <div class="filter-bar">
              <label class="filter-field">
                服务
                <select id="toolServiceFilter" class="tool-service-filter">
                  <option value="">全部服务</option>
                </select>
              </label>
              <label class="filter-field">
                层级
                <select id="toolTypeFilter">
                  <option value="">全部层级</option>
                  <option value="unconfigured">未配置</option>
                  <option value="overview">目录</option>
                  <option value="search">检索</option>
                  <option value="detail">明细</option>
                  <option value="action">操作</option>
                </select>
              </label>
            </div>
            <div class="table-wrap">
              <table class="tools-table">
                <thead>
                  <tr>
                    <th>工具</th>
                    <th>服务</th>
                    <th>层级</th>
                    <th>标签</th>
                    <th>描述</th>
                    <th>配置</th>
                  </tr>
                </thead>
                <tbody id="toolsTableBody">
                  <tr><td colspan="6" class="empty">正在加载工具...</td></tr>
                </tbody>
              </table>
            </div>
            <div id="toolsList" class="tools-list empty" hidden></div>
          </section>
        </section>

        <section class="view" id="view-profiles">
          <div class="panel">
            <div class="panel-heading">
              <div>
                <h2>Project Profile</h2>
                <p>维护服务级白名单/黑名单，供 MetaMCP 和项目接入使用。</p>
              </div>
              <button class="primary" id="openProfileDialog" type="button">添加 Profile</button>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Profile</th>
                    <th>状态</th>
                    <th>Allow</th>
                    <th>Deny</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody id="profilesTable">
                  <tr><td colspan="5" class="empty">正在加载 Project Profile...</td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section class="view" id="view-logs">
          <div class="panel">
            <div class="panel-heading">
              <div>
                <h2>调用日志</h2>
                <p>查看 MetaMCP / 原始工具调用的请求、响应和 log_id。</p>
              </div>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>log_id</th>
                    <th>入口</th>
                    <th>Profile</th>
                    <th>来源</th>
                    <th>工具</th>
                    <th>请求</th>
                    <th>响应</th>
                    <th>耗时</th>
                    <th>状态</th>
                    <th>时间</th>
                  </tr>
                </thead>
                <tbody id="logsTable">
                  <tr><td colspan="10" class="empty">正在加载调用日志...</td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section class="view" id="view-claude">
          <div class="panel">
            <div class="panel-heading">
              <div>
                <h2>Claude Code 接入</h2>
                <p>为当前项目生成 MetaMCP 网关接入命令。</p>
              </div>
            </div>
            <div class="detail-grid">
              <div class="empty">
                <p>建议先创建 Project Profile，再把 profile 作为请求头接入 MetaMCP 网关。</p>
              </div>
              <pre class="json-panel" id="claudeConfig"></pre>
            </div>
          </div>
        </section>
      </main>
    </div>
    <dialog class="modal" id="serviceDialog">
      <form id="serviceForm" class="modal-card" method="dialog">
        <div class="modal-header">
          <div>
            <h2 id="serviceDialogTitle">登记服务</h2>
            <p id="serviceDialogHint">登记内部 MCP 端点并同步其工具定义。</p>
          </div>
          <button class="icon-button" id="closeServiceDialog" type="button" aria-label="关闭">×</button>
        </div>
        <div class="modal-body modal-grid">
          <label>
            服务标识
            <input id="serviceKey" name="service_key" required placeholder="mysql">
          </label>
          <label>
            服务名称
            <input id="serviceName" name="name" required placeholder="MySQL MCP">
          </label>
          <label>
            端点 URL
            <input id="endpointUrl" name="endpoint_url" required placeholder="https://example.test/mcp">
          </label>
          <label>
            请求头 JSON
            <textarea id="headersJson" name="headers" rows="4" placeholder='{"Authorization":"Bearer token"}'></textarea>
          </label>
          <label>
            描述
            <textarea id="description" name="description" rows="3" placeholder="数据库查询能力"></textarea>
          </label>
          <label>
            标签
            <input id="tags" name="tags" placeholder="database, reporting">
          </label>
        </div>
        <div class="modal-actions">
          <button id="cancelServiceDialog" type="button">取消</button>
          <button class="primary" type="submit">保存服务</button>
        </div>
      </form>
    </dialog>
    <dialog class="modal" id="profileDialog">
      <form id="profileForm" class="modal-card" method="dialog">
        <div class="modal-header">
          <div>
            <h2>添加 Project Profile</h2>
            <p>创建项目级能力策略配置，后续可维护服务白名单/黑名单。</p>
          </div>
          <button class="icon-button" id="closeProfileDialog" type="button" aria-label="关闭">×</button>
        </div>
        <div class="modal-body modal-grid">
          <label>
            Profile 标识
            <input id="profileKey" name="profile_key" required placeholder="safe-readonly">
          </label>
          <label>
            Profile 名称
            <input id="profileName" name="name" required placeholder="安全只读">
          </label>
          <label>
            状态
            <select id="profileStatus" name="status">
              <option value="active">启用</option>
              <option value="disabled">停用</option>
            </select>
          </label>
          <label>
            描述
            <textarea id="profileDescription" name="description" rows="3" placeholder="适用于当前项目的能力策略"></textarea>
          </label>
        </div>
        <div class="modal-actions">
          <button id="cancelProfileDialog" type="button">取消</button>
          <button class="primary" type="submit">保存 Profile</button>
        </div>
      </form>
    </dialog>
    <dialog class="modal profile-rules-modal" id="profileRulesDialog">
      <form id="profileRulesForm" class="modal-card" method="dialog">
        <div class="modal-header">
          <div>
            <h2 id="profileRulesDialogTitle">配置 Profile 规则</h2>
            <p id="profileRulesDialogHint">按 MCP 服务设置 Allow / Deny；未设置表示使用默认策略。</p>
          </div>
          <button class="icon-button" id="closeProfileRulesDialog" type="button" aria-label="关闭">×</button>
        </div>
        <div class="modal-body">
          <input id="profileRulesKey" type="hidden">
          <div class="profile-rules-note">
            Allow 为空时默认允许全部服务；配置 Allow 后仅允许 Allow 中的服务；Deny 优先级最高。
          </div>
          <div class="table-wrap">
            <table class="profile-rules-table">
              <thead>
                <tr>
                  <th>服务</th>
                  <th>标签</th>
                  <th>策略</th>
                </tr>
              </thead>
              <tbody id="profileRulesTable">
                <tr><td colspan="3" class="empty">正在读取服务规则...</td></tr>
              </tbody>
            </table>
          </div>
        </div>
        <div class="modal-actions">
          <button id="cancelProfileRulesDialog" type="button">取消</button>
          <button class="primary" type="submit">保存规则</button>
        </div>
      </form>
    </dialog>
    <dialog class="modal compact-modal" id="toolTypeDialog">
      <form id="toolTypeForm" class="modal-card" method="dialog">
        <div class="modal-header">
          <div>
            <h2 id="toolTypeDialogTitle">修改工具层级</h2>
            <p id="toolTypeDialogHint">为工具配置目录、检索、明细或操作层级。</p>
          </div>
          <button class="icon-button" id="closeToolTypeDialog" type="button" aria-label="关闭">×</button>
        </div>
        <div class="modal-body">
          <input id="toolTypeService" type="hidden">
          <input id="toolTypeName" type="hidden">
          <label>
            层级
            <select id="toolTypeValue" required>
              <option value="unconfigured">未配置</option>
              <option value="overview">目录</option>
              <option value="search">检索</option>
              <option value="detail">明细</option>
              <option value="action">操作</option>
            </select>
          </label>
        </div>
        <div class="modal-actions">
          <button id="cancelToolTypeDialog" type="button">取消</button>
          <button class="primary" type="submit">保存配置</button>
        </div>
      </form>
    </dialog>
    <script>window.WIKI_MANAGER_DEFAULT_USER = __WIKI_MANAGER_DEFAULT_USER__;</script>
    <script src="/static/capabilities/app.js" defer></script>
  </body>
</html>
""".replace("__WIKI_MANAGER_DEFAULT_USER__", default_user_json)
