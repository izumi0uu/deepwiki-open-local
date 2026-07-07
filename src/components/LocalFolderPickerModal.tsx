'use client';

import React, { useEffect, useState } from 'react';
import { useLanguage } from '@/contexts/LanguageContext';

interface LocalFolderEntry {
  name: string;
  path: string;
  is_repo_candidate?: boolean;
}

interface LocalFolderRoot {
  name: string;
  path: string;
}

interface BrowseResponse {
  current_path: string;
  parent_path: string | null;
  root_path: string;
  entries: LocalFolderEntry[];
}

interface LocalFolderPickerModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelect: (path: string) => void;
  initialPath?: string;
}

export default function LocalFolderPickerModal({
  isOpen,
  onClose,
  onSelect,
  initialPath,
}: LocalFolderPickerModalProps) {
  const { messages: t } = useLanguage();
  const [roots, setRoots] = useState<LocalFolderRoot[]>([]);
  const [browseData, setBrowseData] = useState<BrowseResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const labels = t.form || {};

  const fetchJson = async <T,>(url: string): Promise<T> => {
    const response = await fetch(url);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `Request failed with status ${response.status}`);
    }
    return data as T;
  };

  const loadRoots = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchJson<{ roots: LocalFolderRoot[] }>('/local_repo/roots');
      setRoots(data.roots || []);
      setBrowseData(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load local folders');
    } finally {
      setIsLoading(false);
    }
  };

  const browsePath = async (path: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchJson<BrowseResponse>(`/local_repo/browse?path=${encodeURIComponent(path)}`);
      setBrowseData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to browse local folder');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    if (initialPath?.trim().startsWith('/')) {
      browsePath(initialPath.trim());
    } else {
      loadRoots();
    }
  }, [isOpen, initialPath]);

  if (!isOpen) return null;

  const currentPath = browseData?.current_path;
  const entries = browseData?.entries || [];

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto">
      <div className="flex min-h-screen items-center justify-center p-4 text-center bg-black/50">
        <div className="relative transform overflow-hidden rounded-lg bg-[var(--card-bg)] text-left shadow-xl transition-all sm:my-8 sm:max-w-2xl sm:w-full">
          <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-color)]">
            <div>
              <h3 className="text-lg font-medium text-[var(--accent-primary)]">
                {labels.selectLocalFolder || 'Select local folder'}
              </h3>
              <p className="mt-1 text-xs text-[var(--muted)]">
                {labels.localFolderHelp || 'Browse folders available to this DeepWiki instance. Only configured allowed roots can be selected.'}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="text-[var(--muted)] hover:text-[var(--foreground)] focus:outline-none transition-colors"
              aria-label={labels.cancel || 'Cancel'}
            >
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div className="p-6 max-h-[70vh] overflow-y-auto">
            {error && (
              <div className="mb-4 rounded-md border border-[var(--highlight)]/30 bg-[var(--highlight)]/10 p-3 text-sm text-[var(--highlight)]">
                {error}
              </div>
            )}

            <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
                  {currentPath ? labels.currentFolder || 'Current folder' : labels.allowedRoots || 'Allowed roots'}
                </div>
                <div className="mt-1 break-all rounded-md border border-[var(--border-color)] bg-[var(--background)]/70 px-3 py-2 text-sm text-[var(--foreground)]">
                  {currentPath || (labels.allowedRoots || 'Allowed roots')}
                </div>
              </div>
              <div className="flex gap-2">
                {browseData?.parent_path && (
                  <button
                    type="button"
                    onClick={() => browsePath(browseData.parent_path as string)}
                    className="px-3 py-2 rounded-md border border-[var(--border-color)] text-sm text-[var(--foreground)] hover:bg-[var(--background)]"
                    disabled={isLoading}
                  >
                    {labels.back || 'Back'}
                  </button>
                )}
                {browseData && (
                  <button
                    type="button"
                    onClick={() => loadRoots()}
                    className="px-3 py-2 rounded-md border border-[var(--border-color)] text-sm text-[var(--foreground)] hover:bg-[var(--background)]"
                    disabled={isLoading}
                  >
                    {labels.allowedRoots || 'Allowed roots'}
                  </button>
                )}
              </div>
            </div>

            {isLoading ? (
              <div className="py-8 text-center text-sm text-[var(--muted)]">
                {t.common?.loading || 'Loading...'}
              </div>
            ) : !browseData ? (
              <div className="space-y-2">
                {roots.length === 0 ? (
                  <div className="rounded-md border border-[var(--border-color)] p-4 text-sm text-[var(--muted)]">
                    {labels.noAllowedRoots || 'No allowed local folder roots are configured.'}
                  </div>
                ) : roots.map((root) => (
                  <button
                    key={root.path}
                    type="button"
                    onClick={() => browsePath(root.path)}
                    className="flex w-full items-center justify-between rounded-md border border-[var(--border-color)] bg-[var(--background)]/40 px-3 py-2 text-left hover:border-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/5"
                  >
                    <span>
                      <span className="block text-sm font-medium text-[var(--foreground)]">{root.name}</span>
                      <span className="block break-all text-xs text-[var(--muted)]">{root.path}</span>
                    </span>
                    <span className="text-[var(--accent-primary)]">›</span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="space-y-4">
                <button
                  type="button"
                  onClick={() => onSelect(browseData.current_path)}
                  className="btn-japanese w-full px-4 py-2.5 rounded-lg"
                >
                  {labels.selectThisFolder || 'Select this folder'}
                </button>

                <div className="space-y-2">
                  {entries.length === 0 ? (
                    <div className="rounded-md border border-[var(--border-color)] p-4 text-sm text-[var(--muted)]">
                      {labels.noSubfolders || 'No subfolders found.'}
                    </div>
                  ) : entries.map((entry) => (
                    <button
                      key={entry.path}
                      type="button"
                      onClick={() => browsePath(entry.path)}
                      className="flex w-full items-center justify-between rounded-md border border-[var(--border-color)] bg-[var(--background)]/40 px-3 py-2 text-left hover:border-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/5"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium text-[var(--foreground)]">
                          📁 {entry.name}
                          {entry.is_repo_candidate ? <span className="ml-2 text-xs text-[var(--accent-primary)]">repo</span> : null}
                        </span>
                        <span className="block break-all text-xs text-[var(--muted)]">{entry.path}</span>
                      </span>
                      <span className="ml-3 text-[var(--accent-primary)]">›</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="flex justify-end gap-3 px-6 py-4 border-t border-[var(--border-color)] bg-[var(--background)]/30">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm rounded-md border border-[var(--border-color)] text-[var(--foreground)] hover:bg-[var(--background)]"
            >
              {labels.cancel || 'Cancel'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
