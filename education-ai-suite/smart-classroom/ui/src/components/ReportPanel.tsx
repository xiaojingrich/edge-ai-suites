import React, { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../redux/hooks';
import {
  clearReportStartRequest,
  reportDone,
  reportFailed,
  startReport,
} from '../redux/slices/uiSlice';
import { streamGenerateReport, getReportDownloadUrl, uploadReportTemplate, getTemplatePreview, type TemplatePreview } from '../services/api';
import { useTranslation } from 'react-i18next';
import '../assets/css/ReportPanel.css';

const activeReportSessions = new Set<string>();

const ReportPanel: React.FC = () => {
  const dispatch = useAppDispatch();
  const { t } = useTranslation();
  const sessionId = useAppSelector(s => s.ui.sessionId);
  const reportStatus = useAppSelector(s => s.ui.reportStatus);
  const reportError = useAppSelector(s => s.ui.reportError);
  const shouldStartReport = useAppSelector(s => s.ui.shouldStartReport);
  const audioStatus = useAppSelector(s => s.ui.audioStatus);

  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const [templatePreview, setTemplatePreview] = useState<TemplatePreview | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const startedRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const previewRef = useRef<HTMLDivElement>(null);

  const showGenerateButton = audioStatus === 'complete' && reportStatus === 'idle' && sessionId;

  useEffect(() => {
    if (showGenerateButton) {
      getTemplatePreview(sessionId).then(setTemplatePreview);
    }
  }, [showGenerateButton, sessionId]);

  useEffect(() => {
    if (!showPreview) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (previewRef.current && !previewRef.current.contains(e.target as Node)) {
        setShowPreview(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showPreview]);

  useEffect(() => {
    if (!sessionId || !shouldStartReport) return;
    if (activeReportSessions.has(sessionId) || startedRef.current) return;

    startedRef.current = true;
    activeReportSessions.add(sessionId);
    dispatch(clearReportStartRequest());

    (async () => {
      try {
        for await (const event of streamGenerateReport(sessionId)) {
          if (event.type === 'report_ready') {
            dispatch(reportDone());
            activeReportSessions.delete(sessionId);
            startedRef.current = false;
            return;
          } else if (event.type === 'error') {
            dispatch(reportFailed(event.message));
            activeReportSessions.delete(sessionId);
            startedRef.current = false;
            return;
          } else if (event.type === 'done') {
            break;
          }
        }
        dispatch(reportDone());
      } catch (err: any) {
        dispatch(reportFailed(err.message));
      } finally {
        activeReportSessions.delete(sessionId);
        startedRef.current = false;
      }
    })();
  }, [sessionId, shouldStartReport, dispatch]);

  useEffect(() => {
    if (sessionId) {
      startedRef.current = false;
      activeReportSessions.delete(sessionId);
    }
  }, [sessionId]);

  const handleGenerate = () => {
    if (!sessionId) return;
    dispatch(startReport());
  };

  const handleRegenerate = () => {
    if (!sessionId) return;
    startedRef.current = false;
    activeReportSessions.delete(sessionId);
    dispatch(startReport());
  };

  const handleTemplateUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      setUploadMsg(null);
      await uploadReportTemplate(file);
      setUploadMsg(t('report.templateUploaded', 'Template uploaded'));
      const preview = await getTemplatePreview(sessionId);
      setTemplatePreview(preview);
      setTimeout(() => setUploadMsg(null), 3000);
    } catch (err: any) {
      setUploadMsg(err.message || t('report.templateUploadFailed', 'Upload failed'));
      setTimeout(() => setUploadMsg(null), 5000);
    }
    e.target.value = '';
  };

  if (!showGenerateButton && reportStatus === 'idle') return null;

  return (
    <div className="report-panel">
      {showGenerateButton && reportStatus === 'idle' && (
        <div className="report-panel-idle">
          <button className="report-generate-btn" onClick={handleGenerate}>
            {t('report.generate', 'Generate Report')}
          </button>
          <button
            className="report-preview-btn"
            onClick={() => setShowPreview(!showPreview)}
            title={t('report.previewTemplate', 'Preview template')}
          >
            {t('report.templatePreview', 'Template')}
          </button>
          {showPreview && (
            <div className="report-template-popover" ref={previewRef}>
              <div className="report-template-popover-header">
                <span className="report-template-popover-title">
                  {templatePreview?.is_custom
                    ? t('report.customTemplate', 'Custom Template')
                    : t('report.defaultTemplate', 'Default Template')}
                </span>
                <button
                  className="report-template-upload-btn"
                  onClick={() => fileInputRef.current?.click()}
                >
                  {t('report.uploadTemplate', 'Upload custom template')}
                </button>
              </div>
              <div className="report-template-popover-body">
                {templatePreview ? (
                  <pre className="report-template-raw">{templatePreview.raw_text}</pre>
                ) : (
                  <span className="report-template-none">{t('report.noTemplate', 'No template found')}</span>
                )}
              </div>
              {uploadMsg && <div className="report-template-popover-msg">{uploadMsg}</div>}
            </div>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".docx"
            style={{ display: 'none' }}
            onChange={handleTemplateUpload}
          />
        </div>
      )}

      {reportStatus === 'generating' && (
        <div className="report-panel-generating">
          <span className="report-spinner"></span>
          <span>{t('report.generating', 'Generating report...')}</span>
        </div>
      )}

      {reportStatus === 'done' && sessionId && (
        <div className="report-panel-done">
          <a
            className="report-download-btn"
            href={getReportDownloadUrl(sessionId)}
            download
          >
            {t('report.download', 'Download Report')}
          </a>
          <button className="report-regenerate-btn" onClick={handleRegenerate}>
            {t('report.regenerate', 'Regenerate')}
          </button>
          <button
            className="report-template-btn"
            onClick={() => fileInputRef.current?.click()}
            title={t('report.uploadTemplate', 'Upload custom template')}
          >
            {t('report.template', 'Template')}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".docx"
            style={{ display: 'none' }}
            onChange={handleTemplateUpload}
          />
          {uploadMsg && <span className="report-upload-msg">{uploadMsg}</span>}
        </div>
      )}

      {reportStatus === 'error' && (
        <div className="report-panel-error">
          <span className="report-error-text">{reportError}</span>
          <button className="report-regenerate-btn" onClick={handleRegenerate}>
            {t('report.retry', 'Retry')}
          </button>
        </div>
      )}
    </div>
  );
};

export default ReportPanel;
