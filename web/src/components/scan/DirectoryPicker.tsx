/**
 * Directory picker — clicking the input opens a popover with a folder browser
 * and a manual path input.
 */

import { useState, useCallback } from 'react';
import { Popover, PopoverTrigger, PopoverContent } from '@rs/ui-new/popover';
import { Button } from '@rs/ui-new/button';
import { Icon } from '@rs/ui-new/icon';
import { Text } from '@rs/ui-new/text';
import { VStack } from '@rs/ui-new/stack';
import { useBrowse } from '../../lib/useBrowse';

interface DirectoryPickerProps {
  value: string;
  onChange: (dir: string) => void;
  disabled?: boolean;
  recentDirs?: string[];
}

export function DirectoryPicker({ value, onChange, disabled, recentDirs }: DirectoryPickerProps) {
  const [open, setOpen] = useState(false);
  const [browsePath, setBrowsePath] = useState<string | undefined>(undefined);
  const { data, isLoading, isError } = useBrowse(browsePath, open);

  const handleOpen = useCallback(
    (nextOpen: boolean) => {
      if (nextOpen) {
        setBrowsePath(value.trim() || undefined);
      }
      setOpen(nextOpen);
    },
    [value],
  );

  const navigateTo = useCallback((path: string) => {
    setBrowsePath(path);
  }, []);

  const handleSelect = useCallback(() => {
    if (data?.current) {
      onChange(data.current);
    }
    setOpen(false);
  }, [data?.current, onChange]);

  // Build breadcrumb segments from the current path
  const breadcrumbs = data?.current
    ? data.current.split('/').reduce<Array<{ label: string; path: string }>>(
        (acc, segment, i) => {
          if (i === 0 && segment === '') {
            acc.push({ label: '/', path: '/' });
          } else if (segment) {
            const parentPath = acc.length > 0 ? acc[acc.length - 1].path : '';
            const fullPath = parentPath === '/' ? `/${segment}` : `${parentPath}/${segment}`;
            acc.push({ label: segment, path: fullPath });
          }
          return acc;
        },
        [],
      )
    : [];

  return (
    <VStack className="gap-1.5 items-start flex-1">
      <Text as="label" level="label-small" className="text-content-layout-2">
        Directory path
      </Text>

      <Popover open={open} onOpenChange={handleOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            className="flex items-center gap-2 h-10 w-full rounded-lg border border-border-layout-1 bg-surface-layout-2 px-3 py-2 text-body-medium text-left cursor-pointer hover:border-border-layout-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Icon
              name="folder-file"
              label=""
              className="w-4 h-4 text-content-layout-3 shrink-0"
            />
            {value.trim() ? (
              <span className="text-content-layout-1 truncate">{value}</span>
            ) : (
              <span className="text-content-layout-3">Choose a project folder...</span>
            )}
          </button>
        </PopoverTrigger>

          <PopoverContent
            side="bottom"
            align="start"
            sideOffset={4}
            variant="layout"
            className="w-[var(--radix-popover-trigger-width)] min-w-80 max-w-none flex-col p-0"
          >
            {/* Recent directories */}
            {recentDirs && recentDirs.length > 0 && (
              <div className="border-b border-border-layout-1">
                <div className="px-3 pt-2 pb-1">
                  <Text level="caption" className="text-content-layout-3">
                    Recent
                  </Text>
                </div>
                {recentDirs.map((dir) => (
                  <button
                    key={dir}
                    type="button"
                    onClick={() => {
                      onChange(dir);
                      setOpen(false);
                    }}
                    className="flex items-center gap-2 w-full px-3 py-1.5 hover:bg-surface-layout-2 cursor-pointer text-left"
                  >
                    <Icon
                      name="folder-file"
                      label=""
                      className="w-3.5 h-3.5 text-content-layout-3"
                    />
                    <Text level="label-small" className="text-content-layout-1 truncate">
                      {dir}
                    </Text>
                  </button>
                ))}
              </div>
            )}

            {/* Breadcrumb bar */}
            <div className="flex items-center gap-1 px-3 py-1.5 border-b border-border-layout-1 overflow-x-auto">
              {breadcrumbs.map((crumb, i) => (
                <span key={crumb.path} className="flex items-center gap-1 shrink-0">
                  {i > 0 && (
                    <Icon
                      name="chevron-right"
                      label=""
                      className="w-3 h-3 text-content-layout-3"
                    />
                  )}
                  <button
                    type="button"
                    onClick={() => navigateTo(crumb.path)}
                    className="text-label-extra-small text-content-layout-2 hover:text-content-layout-1 cursor-pointer whitespace-nowrap"
                  >
                    {crumb.label}
                  </button>
                </span>
              ))}
              {isLoading && (
                <span className="ml-auto text-label-extra-small text-content-layout-3">
                  Loading...
                </span>
              )}
            </div>

            {/* Directory list */}
            <div className="max-h-64 overflow-y-auto">
              {isError && (
                <div className="px-3 py-4 text-center">
                  <Text level="caption" className="text-content-negative-1">
                    Could not read directory
                  </Text>
                </div>
              )}

              {!isError && data && (
                <>
                  {data.parent && (
                    <button
                      type="button"
                      onClick={() => navigateTo(data.parent!)}
                      className="flex items-center gap-2 w-full px-3 py-1.5 hover:bg-surface-layout-2 cursor-pointer text-left"
                    >
                      <Icon
                        name="chevron-left"
                        label=""
                        className="w-3.5 h-3.5 text-content-layout-3"
                      />
                      <Text level="label-small" className="text-content-layout-2">
                        ..
                      </Text>
                    </button>
                  )}

                  {data.directories.map((dir) => (
                    <button
                      key={dir.path}
                      type="button"
                      onClick={() => navigateTo(dir.path)}
                      className="flex items-center gap-2 w-full px-3 py-1.5 hover:bg-surface-layout-2 cursor-pointer text-left"
                    >
                      <Icon
                        name="folder-file"
                        label=""
                        className="w-3.5 h-3.5 text-content-layout-3"
                      />
                      <Text level="label-small" className="text-content-layout-1 truncate">
                        {dir.name}
                      </Text>
                    </button>
                  ))}

                  {data.directories.length === 0 && (
                    <div className="px-3 py-4 text-center">
                      <Text level="caption" className="text-content-layout-3">
                        No subdirectories
                      </Text>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Select button */}
            <div className="border-t border-border-layout-1 px-3 py-2">
              <Button
                variant="rising"
                modifier="solid"
                label="Select this folder"
                onClick={handleSelect}
                className="w-full"
                disabled={!data?.current}
              />
            </div>
          </PopoverContent>
      </Popover>
    </VStack>
  );
}
