'use client';

import React from 'react';

interface Props {
    children: React.ReactNode;
    fallback?: (error: Error, reset: () => void) => React.ReactNode;
}

interface State {
    error: Error | null;
}

/**
 * Catches render-time exceptions in the chat/editor tree so a buggy message
 * render or streaming parser crash doesn't blank the whole IDE. Without this,
 * any thrown error unmounts the entire app silently.
 */
export default class ErrorBoundary extends React.Component<Props, State> {
    state: State = { error: null };

    static getDerivedStateFromError(error: Error): State {
        return { error };
    }

    componentDidCatch(error: Error, info: React.ErrorInfo) {
        // eslint-disable-next-line no-console
        console.error('[ErrorBoundary]', error, info.componentStack);
    }

    reset = () => this.setState({ error: null });

    render() {
        if (this.state.error) {
            if (this.props.fallback) return this.props.fallback(this.state.error, this.reset);
            return (
                <div
                    role="alert"
                    className="flex flex-col items-start gap-2 p-4 m-3 rounded-md"
                    style={{
                        background: 'var(--bg-panel)',
                        border: '1px solid var(--red, #ef4444)',
                        color: 'var(--text-primary)',
                    }}
                >
                    <strong style={{ color: 'var(--red, #ef4444)' }}>Something went wrong.</strong>
                    <pre className="text-xs overflow-auto max-h-40 w-full whitespace-pre-wrap"
                        style={{ color: 'var(--text-muted)' }}>
                        {this.state.error.message}
                    </pre>
                    <button
                        onClick={this.reset}
                        className="text-xs px-2 py-1 rounded"
                        style={{ background: 'var(--bg-hover)', color: 'var(--text-primary)' }}
                    >
                        Retry
                    </button>
                </div>
            );
        }
        return this.props.children;
    }
}
