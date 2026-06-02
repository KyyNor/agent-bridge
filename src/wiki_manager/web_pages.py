from __future__ import annotations


def capability_admin_page() -> str:
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Agent Capability Hub</title>
    <link rel="stylesheet" href="/static/capabilities/app.css">
  </head>
  <body>
    <div class="layout">
      <aside class="sidebar" aria-label="Capability navigation">
        <div class="sidebar-header">
          <div class="sidebar-logo">
            <div class="sidebar-logo-icon">A</div>
            <div class="sidebar-logo-text">
              Agent Capability Hub
              <span>MCP registration</span>
            </div>
          </div>
        </div>
        <nav class="sidebar-nav">
          <div class="nav-group-label">Admin</div>
          <a class="nav-item active" href="/admin/capabilities">MCP Services</a>
        </nav>
        <div class="sidebar-footer">
          <span class="status-dot"></span>
          Phase 1 service registry
        </div>
      </aside>

      <main class="main">
        <header class="topbar">
          <div>
            <p class="eyebrow">Capability Admin</p>
            <h1>Agent Capability Hub</h1>
          </div>
          <button class="primary" id="refreshServices" type="button">Refresh</button>
        </header>

        <section class="message-area" id="messageArea" role="status" aria-live="polite"></section>

        <div class="content-grid">
          <section class="panel services-panel">
            <div class="panel-heading">
              <div>
                <h2>MCP Services</h2>
                <p>Register internal MCP endpoints and sync their exposed tools.</p>
              </div>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>Endpoint</th>
                    <th>Status</th>
                    <th>Tags</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody id="servicesTable">
                  <tr><td colspan="5" class="empty">Loading services...</td></tr>
                </tbody>
              </table>
            </div>
          </section>

          <section class="panel form-panel">
            <div class="panel-heading">
              <div>
                <h2>Register Service</h2>
                <p>Create or update a service using the same service key.</p>
              </div>
            </div>
            <form id="serviceForm">
              <label>
                Service key
                <input id="serviceKey" name="service_key" required placeholder="mysql">
              </label>
              <label>
                Name
                <input id="serviceName" name="name" required placeholder="MySQL MCP">
              </label>
              <label>
                Endpoint URL
                <input id="endpointUrl" name="endpoint_url" required placeholder="https://example.test/mcp">
              </label>
              <label>
                Headers JSON
                <textarea id="headersJson" name="headers" rows="4" placeholder='{"Authorization":"Bearer token"}'></textarea>
              </label>
              <label>
                Description
                <textarea id="description" name="description" rows="3" placeholder="Database query tools"></textarea>
              </label>
              <label>
                Tags
                <input id="tags" name="tags" placeholder="database, reporting">
              </label>
              <button class="primary full" type="submit">Save Service</button>
            </form>
          </section>

          <section class="panel tools-panel">
            <div class="panel-heading">
              <div>
                <h2>Selected Service Tools</h2>
                <p id="selectedServiceHint">Select a service to inspect synced tools.</p>
              </div>
              <button id="reloadTools" type="button" disabled>Reload Tools</button>
            </div>
            <div id="toolsList" class="tools-list empty">No service selected.</div>
          </section>
        </div>
      </main>
    </div>
    <script src="/static/capabilities/app.js" defer></script>
  </body>
</html>
"""
