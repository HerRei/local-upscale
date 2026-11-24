// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import SetupScreen from './SetupScreen.svelte';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function platform(value: string) {
  vi.stubGlobal('navigator', { platform: value });
}
const next = async (user: ReturnType<typeof userEvent.setup>) =>
  user.click(screen.getByRole('button', { name: 'Continue' }));

it('shows privacy defaults without descriptions, then navigates and retains choices with focus', async () => {
  platform('MacIntel');
  const user = userEvent.setup();
  render(SetupScreen);
  expect(screen.getByRole('heading', { name: 'Privacy' })).toBeTruthy();
  expect(screen.getByLabelText('Step 1 of 2').textContent).toBe('1 of 2');
  expect(
    screen.getByText(
      'Your photos and videos stay on this computer. Nothing is sent until you finish setup.',
    ),
  ).toBeTruthy();
  expect(screen.getAllByRole('switch').map((el) => el.id)).toEqual([
    'setup-check-updates',
    'setup-anonymous-count',
    'setup-crash-reports',
  ]);
  expect(
    screen
      .getAllByRole('button')
      .filter((el) => el.tagName === 'BUTTON')
      .map((el) => el.textContent?.trim()),
  ).toEqual(['Continue']);
  for (const item of screen.getAllByRole('switch'))
    expect((item as HTMLInputElement).checked).toBe(true);
  expect(screen.queryAllByRole('radio')).toHaveLength(0);
  expect(document.querySelector('.setting-desc')).toBeNull();
  await user.click(screen.getByLabelText('Offer to report crashes'));
  await next(user);
  expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Look and feel' }));
  expect(screen.getByLabelText('Step 2 of 2')).toBeTruthy();
  expect(screen.getAllByRole('radio')).toHaveLength(3);
  expect(screen.getAllByRole('switch')).toHaveLength(4);
  expect(document.querySelector('.setting-desc')).toBeNull();
  await user.click(screen.getByRole('radio', { name: 'Light' }));
  await user.click(screen.getByRole('button', { name: 'Back' }));
  expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Privacy' }));
  expect((screen.getByLabelText('Offer to report crashes') as HTMLInputElement).checked).toBe(
    false,
  );
  await next(user);
  expect(screen.getByRole('radio', { name: 'Light' }).getAttribute('aria-checked')).toBe('true');
});

it.each([
  [
    'MacIntel',
    'menu bar',
    'An icon in the menu bar shows what is running and how many files wait, with Pause and Resume.',
  ],
  [
    'Win32',
    'system tray',
    'An icon in the system tray shows what is running and how many files wait, with Pause and Resume. Windows may keep it behind the ^ arrow next to the clock.',
  ],
  [
    'Linux x86_64',
    'system tray',
    'An icon in the system tray shows what is running and how many files wait, with Pause and Resume. Some desktops, such as GNOME without an extension, do not show tray icons.',
  ],
])('shows platform wording and tips on %s', async (os, name, excerpt) => {
  platform(os);
  const user = userEvent.setup();
  render(SetupScreen);
  await user.tab();
  expect(screen.getByRole('tooltip').textContent).toContain(
    os === 'MacIntel' ? 'LocalSR menu' : 'Help menu',
  );
  await next(user);
  expect(screen.getByLabelText(`Show LocalSR in the ${name}`)).toBeTruthy();
  await user.tab();
  await user.tab();
  await user.tab();
  await user.tab();
  await user.tab();
  await user.tab();
  await user.tab();
  expect(screen.getByRole('tooltip').textContent).toBe(excerpt);
  expect(
    (screen.getByLabelText('Play a sound when a file finishes') as HTMLInputElement).checked,
  ).toBe(false);
});

it('opens each privacy explanation with its exact text', async () => {
  platform('MacIntel');
  const user = userEvent.setup();
  render(SetupScreen);
  const entries = [
    [
      'Check for updates when LocalSR starts',
      'Reads the update feed on GitHub Pages once at launch and tells you when a newer version exists. Nothing about you or your files is sent; GitHub sees your IP address, as any website does. With this off, Check for Updates… in the LocalSR menu still works.',
    ],
    [
      'Send an anonymous count with each update check',
      "After an update check, sends the app version, platform and release channel to the developer's server. No ID, no cookies, nothing about your files, and your IP address is not stored. It shows how many copies of LocalSR are in use.",
    ],
    [
      'Offer to report crashes',
      'After a crash, LocalSR asks at the next start whether you want to report it. Reports stay on this computer and are never sent automatically: you read the report first and decide whether to open a GitHub issue with it. File names and paths are removed.',
    ],
  ];
  for (const [label, text] of entries) {
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: `About ${label}` }));
    expect(screen.getByRole('tooltip').textContent).toBe(text);
    await user.tab();
  }
});

it('explains high contrast and sound', async () => {
  const user = userEvent.setup();
  render(SetupScreen);
  await next(user);
  for (let i = 0; i < 5; i++) await user.tab();
  expect(screen.getByRole('tooltip').textContent).toBe(
    'Stronger text, borders and focus outlines. Also turns on by itself when your computer asks for more contrast.',
  );
  for (let i = 0; i < 4; i++) await user.tab();
  expect(screen.getByRole('tooltip').textContent).toBe(
    "Plays your system's notification sound with the notification that a file is finished or has failed. Notifications stay on for this; if your computer mutes them, there is no sound.",
  );
});

it.each([
  ['system', false],
  ['standard', false],
  ['high', true],
] as const)('previews contrast %s and preserves untouched value', async (contrast, checked) => {
  const user = userEvent.setup();
  const preview = vi.fn(),
    finish = vi.fn();
  render(SetupScreen, { props: { contrast }, events: { preview, finish } });
  await next(user);
  expect((screen.getByLabelText('High contrast') as HTMLInputElement).checked).toBe(checked);
  await user.click(screen.getByRole('button', { name: 'Done' }));
  expect(finish.mock.calls[0][0].detail.contrast).toBe(contrast);
  await user.click(screen.getByLabelText('High contrast'));
  expect(preview.mock.calls.at(-1)?.[0].detail.contrast).toBe(checked ? 'system' : 'high');
  await user.click(screen.getByLabelText('High contrast'));
  expect(preview.mock.calls.at(-1)?.[0].detail.contrast).toBe(checked ? 'high' : 'system');
});

it('dispatches only at Done with all choices and preserves hidden Store fields', async () => {
  const user = userEvent.setup();
  const finish = vi.fn();
  render(SetupScreen, {
    props: { storeBuild: true, checkAtLaunch: false, anonymousCount: true },
    events: { finish },
  });
  expect(screen.getAllByRole('switch')).toHaveLength(1);
  expect(screen.getByText('You can change all of this later in Settings → Privacy.')).toBeTruthy();
  await next(user);
  await user.click(screen.getByRole('button', { name: 'Back' }));
  await next(user);
  expect(finish).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Done' }));
  expect(finish.mock.calls[0][0].detail).toEqual({
    appearance: 'dark',
    colourBlind: false,
    contrast: 'system',
    checkAtLaunch: false,
    anonymousCount: true,
    crashReports: true,
    menuBar: true,
    sound: false,
  });
});

it('shows save errors and disables both actions while saving', async () => {
  const user = userEvent.setup();
  render(SetupScreen, { saving: true, error: 'Could not save' });
  await next(user);
  expect(screen.getByRole('alert').textContent).toBe('Could not save');
  expect((screen.getByRole('button', { name: 'Back' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'Saving…' }) as HTMLButtonElement).disabled).toBe(
    true,
  );
});

it('traps focus in each page and ignores Escape', async () => {
  const user = userEvent.setup();
  render(SetupScreen);
  const dialog = screen.getByRole('dialog');
  const privacyOrder = [
    screen.getByRole('button', { name: 'About Check for updates when LocalSR starts' }),
    screen.getByRole('switch', { name: 'Check for updates when LocalSR starts' }),
    screen.getByRole('button', { name: 'About Send an anonymous count with each update check' }),
    screen.getByRole('switch', { name: 'Send an anonymous count with each update check' }),
    screen.getByRole('button', { name: 'About Offer to report crashes' }),
    screen.getByRole('switch', { name: 'Offer to report crashes' }),
    screen.getByRole('button', { name: 'Continue' }),
  ];
  for (const control of [...privacyOrder, privacyOrder[0]]) {
    await user.tab();
    expect(document.activeElement).toBe(control);
  }
  await user.keyboard('{Escape}');
  expect(screen.getByRole('dialog')).toBe(dialog);
  await next(user);
  const trayName = /Mac/.test(navigator.platform) ? 'menu bar' : 'system tray';
  const lookOrder = [
    ...screen.getAllByRole('radio'),
    screen.getByRole('switch', { name: 'Colour-blind friendly colours' }),
    screen.getByRole('button', { name: 'About High contrast' }),
    screen.getByRole('switch', { name: 'High contrast' }),
    screen.getByRole('button', { name: `About Show LocalSR in the ${trayName}` }),
    screen.getByRole('switch', { name: `Show LocalSR in the ${trayName}` }),
    screen.getByRole('button', { name: 'About Play a sound when a file finishes' }),
    screen.getByRole('switch', { name: 'Play a sound when a file finishes' }),
    screen.getByRole('button', { name: 'Back' }),
    screen.getByRole('button', { name: 'Done' }),
  ];
  for (const control of [...lookOrder, lookOrder[0]]) {
    await user.tab();
    expect(document.activeElement).toBe(control);
  }
  await user.keyboard('{Escape}');
  expect(within(dialog).getByRole('heading', { name: 'Look and feel' })).toBeTruthy();
});
