# 前端开发指南

## 技术栈

本系统前端采用现代化技术栈：
- React 18 + TypeScript
- Vite 5 构建工具
- Tailwind CSS 样式框架
- React Router v6 路由管理
- React Query 状态管理
- Zustand 全局状态

## 项目结构

```
src/
├── components/        # 可复用组件
│   ├── ui/           # 基础UI组件
│   ├── forms/        # 表单组件
│   └── layouts/      # 布局组件
├── pages/            # 页面组件
├── hooks/            # 自定义Hooks
├── services/         # API服务
├── store/            # 状态管理
├── utils/            # 工具函数
├── types/            # TypeScript类型
└── App.tsx           # 应用入口
```

## 组件开发

### 函数式组件 + Hooks

```tsx
import { useState, useEffect } from 'react';
import { useUserStore } from '@/store/user';

interface UserCardProps {
  userId: string;
  onEdit?: (id: string) => void;
}

export function UserCard({ userId, onEdit }: UserCardProps) {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<User | null>(null);

  const { fetchUser } = useUserStore();

  useEffect(() => {
    fetchUser(userId).then(data => {
      setUser(data);
      setLoading(false);
    });
  }, [userId, fetchUser]);

  if (loading) return <SkeletonCard />;
  if (!user) return <ErrorMessage />;

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <div className="flex items-center space-x-4">
        <img
          src={user.avatar}
          alt={user.name}
          className="w-16 h-16 rounded-full"
        />
        <div>
          <h3 className="text-lg font-semibold">{user.name}</h3>
          <p className="text-gray-500">{user.email}</p>
        </div>
        {onEdit && (
          <button
            onClick={() => onEdit(user.id)}
            className="ml-auto px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
          >
            编辑
          </button>
        )}
      </div>
    </div>
  );
}
```

### 自定义 Hooks

```tsx
// hooks/useDebounce.ts
import { useEffect, useState } from 'react';

export function useDebounce<T>(value: T, delay: number = 500): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    return () => clearTimeout(handler);
  }, [value, delay]);

  return debouncedValue;
}

// 使用示例
function SearchInput() {
  const [query, setQuery] = useState('');
  const debouncedQuery = useDebounce(query, 300);

  useEffect(() => {
    // 使用防抖后的查询值发起搜索
    performSearch(debouncedQuery);
  }, [debouncedQuery]);

  return (
    <input
      type="text"
      value={query}
      onChange={(e) => setQuery(e.target.value)}
      placeholder="搜索..."
    />
  );
}
```

## 状态管理

### React Query（服务器状态）

```tsx
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';

function UserProfile({ userId }: { userId: string }) {
  const queryClient = useQueryClient();

  // 获取数据
  const { data: user, isLoading, error } = useQuery({
    queryKey: ['user', userId],
    queryFn: () => api.users.getById(userId),
    staleTime: 5 * 60 * 1000, // 5分钟
  });

  // 更新数据
  const updateMutation = useMutation({
    mutationFn: (data: UpdateUserDto) =>
      api.users.update(userId, data),
    onSuccess: () => {
      // 使缓存失效，触发重新获取
      queryClient.invalidateQueries({ queryKey: ['user', userId] });
    },
  });

  if (isLoading) return <LoadingSpinner />;
  if (error) return <ErrorMessage error={error} />;

  return (
    <div>
      <h1>{user.name}</h1>
      <button onClick={() => updateMutation.mutate({ name: 'New Name' })}>
        更新名称
      </button>
    </div>
  );
}
```

### Zustand（客户端状态）

```tsx
// store/useAuthStore.ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface AuthState {
  user: User | null;
  token: string | null;
  setAuth: (user: User, token: string) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      setAuth: (user, token) => set({ user, token }),
      logout: () => set({ user: null, token: null }),
    }),
    {
      name: 'auth-storage',
    }
  )
);

// 使用示例
function Header() {
  const { user, logout } = useAuthStore();

  return (
    <header>
      <span>欢迎, {user?.name}</span>
      <button onClick={logout}>退出</button>
    </header>
  );
}
```

## 路由配置

```tsx
// App.tsx
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Layout } from '@/components/layouts/Layout';
import { Dashboard } from '@/pages/Dashboard';
import { Login } from '@/pages/Login';
import { ProtectedRoute } from '@/components/ProtectedRoute';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<Layout />}>
            <Route path="/" element={<Navigate to="/dashboard" />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/users" element={<UserList />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

// ProtectedRoute.tsx
import { useAuthStore } from '@/store/auth';

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { token } = useAuthStore();

  if (!token) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}
```

## 表单处理

```tsx
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';

const loginSchema = z.object({
  email: z.string().email('邮箱格式不正确'),
  password: z.string().min(8, '密码至少8个字符'),
});

type LoginForm = z.infer<typeof loginSchema>;

function LoginForm() {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
  });

  const onSubmit = async (data: LoginForm) => {
    try {
      await api.auth.login(data);
      // 登录成功
    } catch (error) {
      // 处理错误
    }
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
      <div>
        <label className="block">邮箱</label>
        <input
          type="email"
          {...register('email')}
          className="w-full px-3 py-2 border rounded"
        />
        {errors.email && (
          <p className="text-red-500 text-sm">{errors.email.message}</p>
        )}
      </div>

      <div>
        <label className="block">密码</label>
        <input
          type="password"
          {...register('password')}
          className="w-full px-3 py-2 border rounded"
        />
        {errors.password && (
          <p className="text-red-500 text-sm">{errors.password.message}</p>
        )}
      </div>

      <button
        type="submit"
        disabled={isSubmitting}
        className="w-full py-2 bg-blue-500 text-white rounded disabled:bg-gray-400"
      >
        {isSubmitting ? '登录中...' : '登录'}
      </button>
    </form>
  );
}
```

## 样式管理

### Tailwind CSS

```tsx
// 响应式设计
<div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
  {items.map(item => (
    <Card key={item.id} item={item} />
  ))}
</div>

// 暗色模式
<div className="bg-white dark:bg-gray-800 text-gray-900 dark:text-white">
  暗色模式支持
</div>

// 自定义配置
// tailwind.config.js
export default {
  content: ['./src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#f0f9ff',
          500: '#0ea5e9',
          900: '#0c4a6e',
        },
      },
    },
  },
};
```

### CSS Modules

```tsx
// UserProfile.module.css
.container {
  composes: card from './common.module.css';
  padding: 1rem;
}

.title {
  font-size: 1.5rem;
  font-weight: bold;
}

// UserProfile.tsx
import styles from './UserProfile.module.css';

export function UserProfile() {
  return (
    <div className={styles.container}>
      <h1 className={styles.title}>用户资料</h1>
    </div>
  );
}
```

## 性能优化

### 代码分割

```tsx
import { lazy, Suspense } from 'react';

const Dashboard = lazy(() => import('@/pages/Dashboard'));
const Settings = lazy(() => import('@/pages/Settings'));

function App() {
  return (
    <Suspense fallback={<LoadingSpinner />}>
      <Routes>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </Suspense>
  );
}
```

### 虚拟化长列表

```tsx
import { useVirtualizer } from '@tanstack/react-virtual';

function VirtualList({ items }: { items: Item[] }) {
  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 50, // 每项高度
  });

  return (
    <div ref={parentRef} className="h-96 overflow-auto">
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
        }}
      >
        {virtualizer.getVirtualItems().map((virtualItem) => (
          <div
            key={virtualItem.key}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: `${virtualItem.size}px`,
              transform: `translateY(${virtualItem.start}px)`,
            }}
          >
            {items[virtualItem.index].content}
          </div>
        ))}
      </div>
    </div>
  );
}
```

### memo 和 useMemo

```tsx
import { memo, useMemo } from 'react';

const ExpensiveComponent = memo(function ExpensiveComponent({
  data,
}: {
  data: ComplexData;
}) {
  const processed = useMemo(() => {
    return expensiveComputation(data);
  }, [data]);

  return <div>{processed}</div>;
});
```

## 测试

### 单元测试

```tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LoginForm } from './LoginForm';

describe('LoginForm', () => {
  it('should show validation errors', async () => {
    render(<LoginForm />);

    fireEvent.click(screen.getByText('登录'));

    await waitFor(() => {
      expect(screen.getByText('邮箱格式不正确')).toBeInTheDocument();
    });
  });
});
```

## 构建和部署

```bash
# 开发环境
npm run dev

# 类型检查
npm run type-check

# 代码检查
npm run lint

# 生产构建
npm run build

# 预览构建
npm run preview
```
