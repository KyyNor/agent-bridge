# Agent Bridge 访问上下文

本上下文描述 Agent Bridge 如何识别调用者，以及业务资源在内部协作中的归属与可见范围。

## Language

**调用者（Actor）**:
发起 Agent Bridge 操作的内部用户，以统一账户系统的用户 ID 作为唯一身份。
_Avoid_: 前端用户、请求用户、Linux 用户

**数据组（Group）**:
内部用户当前所属的协作小组，也是私有业务资源的访问边界。
_Avoid_: 角色、部门、租户

**资源归属组（Owner Group）**:
业务资源创建时确定的所属数据组；创建者后来换组不会改变资源归属。
_Avoid_: 创建人、资源管理员

**组内资源（Group Resource）**:
仅资源归属组成员可以读取、使用和修改的业务资源。
_Avoid_: 私有资源、个人资源

**共享资源（Shared Resource）**:
所有已识别调用者均可读取和使用、但仍仅由资源归属组修改的业务资源。
_Avoid_: 公共资源、全局资源

**维护管理员（Maintenance Admin）**:
由 `server.toml` 的 `admins` 指定、用于故障处理和存量迁移的维护旁路身份。
_Avoid_: 超级用户、业务管理员
