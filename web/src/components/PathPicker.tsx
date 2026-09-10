/**
 * Server-side path field — a real text input that accepts a typed or pasted
 * path, with Browse opening a popover folder browser beside it. Selects a
 * directory by default; pass `fileExt` to pick a file with that extension.
 */

import { BaseInputText } from '@rs/ui-new/base-input-text';
import { Button } from '@rs/ui-new/button';
import { Icon } from '@rs/ui-new/icon';
import { Label } from '@rs/ui-new/label';
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover';
import { Pressable } from '@rs/ui-new/pressable';
import { Scrollable } from '@rs/ui-new/scrollable';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Text } from '@rs/ui-new/text';
import { useCallback, useId, useState } from 'react';
import { useBrowse } from '../lib/useBrowse';

interface PathPickerProps {
  value: string;
  onChange: (path: string) => void;
  disabled?: boolean;
  recentDirs?: string[];
  /** When set, the picker selects a file with this extension (e.g. "csv"). */
  fileExt?: string;
  label?: string;
  placeholder?: string;
}

export function PathPicker({
  value,
  onChange,
  disabled,
  recentDirs,
  fileExt,
  label,
  placeholder,
}: PathPickerProps) {
  const inputId = useId();
  const [open, setOpen] = useState(false);
  const [browsePath, setBrowsePath] = useState<string | undefined>(undefined);
  const { data, isLoading, isError } = useBrowse(browsePath, open, fileExt);

  const handleOpen = useCallback(
    (nextOpen: boolean) => {
      if (nextOpen) {
        // A previously selected file can't be browsed into; start at its folder.
        let start = value.trim();
        if (fileExt && start.toLowerCase().endsWith(`.${fileExt.toLowerCase()}`)) {
          start = start.slice(0, start.lastIndexOf('/')) || '/';
        }
        setBrowsePath(start || undefined);
      }
      setOpen(nextOpen);
    },
    [value, fileExt],
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

  const files = fileExt ? (data?.files ?? []) : [];

  return (
    <VStack className="gap-1.5 items-start flex-1">
      <Label htmlFor={inputId}>
        {label ?? (fileExt ? 'File path' : 'Directory path')}
      </Label>

      <Popover open={open} onOpenChange={handleOpen}>
        {/* The path is typed or pasted; browsing is the secondary way in. A
            picker whose one-click default is $HOME made "scan my entire home
            directory" the shortest route through the flow. [E-16] The
            recent/breadcrumb/parent/dir/file rows in the popover stay
            hand-rolled: they are menu items, not Button candidates. */}
        <HStack className="gap-2 items-center w-full">
          <BaseInputText
            id={inputId}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            disabled={disabled}
            spellCheck={false}
            autoComplete="off"
            icon={fileExt ? 'document-validation' : 'folder-file'}
            iconPosition="left"
            placeholder={
              placeholder ??
              (fileExt ? `/path/to/queries.${fileExt}` : '/path/to/your/project')
            }
            title={value || undefined}
          />
          <PopoverTrigger asChild>
            <Button
              variant="primary"
              modifier="outline"
              label="Browse"
              disabled={disabled}
              className="shrink-0"
            />
          </PopoverTrigger>
        </HStack>

          <PopoverContent
            side="bottom"
            align="end"
            sideOffset={4}
            variant="layout"
            className="min-w-96 max-w-none flex-col p-0"
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
                  <Pressable
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
                  </Pressable>
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
                  <Pressable
                    type="button"
                    onClick={() => navigateTo(crumb.path)}
                    className="text-label-extra-small text-content-layout-2 hover:text-content-layout-1 cursor-pointer whitespace-nowrap"
                  >
                    {crumb.label}
                  </Pressable>
                </span>
              ))}
              {isLoading && (
                <span className="ml-auto text-label-extra-small text-content-layout-3">
                  Loading...
                </span>
              )}
            </div>

            {/* Directory + file list */}
            <Scrollable className="max-h-64">
              {isError && (
                <div className="px-3 py-4 text-center">
                  <Text level="caption" className="text-content-negative-soft">
                    Could not read directory
                  </Text>
                </div>
              )}

              {!isError && data && (
                <>
                  {data.parent && (
                    <Pressable
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
                    </Pressable>
                  )}

                  {data.directories.map((dir) => (
                    <Pressable
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
                    </Pressable>
                  ))}

                  {files.map((file) => (
                    <Pressable
                      key={file.path}
                      type="button"
                      onClick={() => {
                        onChange(file.path);
                        setOpen(false);
                      }}
                      className="flex items-center gap-2 w-full px-3 py-1.5 hover:bg-surface-layout-2 cursor-pointer text-left"
                    >
                      <Icon
                        name="document-validation"
                        label=""
                        className="w-3.5 h-3.5 text-content-layout-3"
                      />
                      <Text level="label-small" className="text-content-layout-1 truncate">
                        {file.name}
                      </Text>
                    </Pressable>
                  ))}

                  {data.directories.length === 0 && files.length === 0 && (
                    <div className="px-3 py-4 text-center">
                      <Text level="caption" className="text-content-layout-3">
                        {fileExt ? `No folders or .${fileExt} files` : 'No subdirectories'}
                      </Text>
                    </div>
                  )}
                </>
              )}
            </Scrollable>

            {/* Select button (directory mode only; files are picked directly).
                The home directory is never selectable in one click: a scan of
                every project at once is nobody's intent, so the reader has to
                walk into the folder they mean. [E-16] */}
            {!fileExt && (
              <div className="border-t border-border-layout-1 px-3 py-2">
                <Button
                  variant="primary"
                  modifier="solid"
                  label="Select this folder"
                  onClick={handleSelect}
                  className="w-full"
                  disabled={!data?.current || data.is_home}
                />
                {data?.is_home && (
                  <Text
                    level="caption"
                    className="text-content-layout-3 mt-1.5 block text-center"
                  >
                    Open the project you want to scan, then select it.
                  </Text>
                )}
              </div>
            )}
          </PopoverContent>
      </Popover>
    </VStack>
  );
}
