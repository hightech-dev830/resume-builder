const { BrowserWindow } = require('electron');

// Renders an HTML string to a PDF buffer in a hidden window. Uses the
// template's @page rules for size and margins.
async function htmlToPdf(html) {
  const win = new BrowserWindow({
    show: false,
    width: 816,
    height: 1056,
    webPreferences: { javascript: false, sandbox: true }
  });
  try {
    await win.loadURL('data:text/html;charset=utf-8;base64,' + Buffer.from(html, 'utf8').toString('base64'));
    return await win.webContents.printToPDF({
      printBackground: true,
      preferCSSPageSize: true,
      pageSize: 'Letter'
    });
  } finally {
    win.destroy();
  }
}

module.exports = { htmlToPdf };
