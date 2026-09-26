import { useState, useEffect } from 'react';
import './Settings.css';

const DEFAULT_PIPELINE = {
  title: true,
  slug: true,
  description: true,
  tags: true,
  pinned_comment: true,
  quiz: true,
  chapters: true,
  thumbnail_with_text: true,
  thumbnail_without_text: true,
  audio: true,
  video_render: false,
  youtube_upload: false,
  youtube_schedule: false
};

const GOOGLE_FLOW_IMAGE_MODELS = [
  {
    id: 'nano_banana_2',
    name: '🍌 Nano Banana 2',
    badge: '🟢 Chuẩn Google Flow',
    creditLabel: '🟢 Tiết kiệm (~1 credit/ảnh)',
    speed: '⚡ 3–5s',
    description: '⭐ Model tạo ảnh mặc định mới nhất trên Google Flow. Tốc độ sinh nhanh, màu sắc chân thực, chi tiết sắc nét, phù hợp tạo 30–50 cảnh visual cho video dài.'
  },
  {
    id: 'nano_banana_pro',
    name: 'Nano Banana Pro (Legacy)',
    badge: '🟢 Tiết kiệm Credit',
    creditLabel: '🟢 Thấp nhất (~1 credit/ảnh)',
    speed: '⚡ 3–5s',
    description: 'Model thế hệ tiền nhiệm, tương thích hoàn toàn với các prompt version cũ.',
    recommended: true
  },
  {
    id: 'imagen_3_standard',
    name: 'Google Imagen 3 (High-Fidelity)',
    badge: '🟡 Chất lượng cao',
    creditLabel: '🟡 Trung bình (~2–3 credit/ảnh)',
    speed: '⏳ 8–12s',
    description: 'Chất lượng siêu thực cao cấp. Tái tạo biểu cảm nhân vật, bàn tay, ánh sáng và chi tiết da xuất sắc; bám sát prompt phức tạp.'
  },
  {
    id: 'imagen_3_photoreal',
    name: 'Google Imagen 3 (Photorealistic / 35mm)',
    badge: '🟡 Tư liệu thực tế',
    creditLabel: '🟡 Tiêu chuẩn (~2 credit/ảnh)',
    speed: '⏳ 6–10s',
    description: 'Phong cách ảnh tư liệu / Điện ảnh thực tế. Tối ưu đặc biệt cho video kể chuyện, phim tài liệu, hạn chế cảm giác bóng bẩy 3D/CGI.'
  }
];

const GOOGLE_FLOW_VIDEO_MODELS = [
  {
    id: 'omni_1_1_flash',
    name: 'Omni 1.1 Flash',
    badge: '⚡ Mặc định / Siêu tốc',
    creditLabel: '🟡 Tiêu chuẩn (~10 credit/clip)',
    speed: '⚡ 15–25s',
    description: '⭐ Model tạo video AI mặc định mới nhất của Google Flow. Tốc độ sinh siêu nhanh, chuyển động mượt mà và phối cảnh nhất quán.'
  },
  {
    id: 'veo_3_1_lite',
    name: 'Veo 3.1 – Lite',
    badge: '🟢 Tiết kiệm Credit',
    creditLabel: '🟢 Thấp (~8 credit/clip)',
    speed: '⏳ 20–30s',
    description: 'Bản rút gọn của Veo 3.1, tối ưu chi phí credit cho các cảnh intro ngắn.',
    recommended: true
  },
  {
    id: 'veo_3_1_fast',
    name: 'Veo 3.1 – Fast',
    badge: '🟡 Tốc độ cao',
    creditLabel: '🟡 Trung bình (~12 credit/clip)',
    speed: '⏳ 25–40s',
    description: 'Veo 3.1 phiên bản tối ưu tốc độ, cân bằng chuyển động điện ảnh và thời gian chờ.'
  },
  {
    id: 'veo_3_1_quality',
    name: 'Veo 3.1 – Quality',
    badge: '🔴 Chất lượng Điện ảnh',
    creditLabel: '🔴 Cao (~15–20 credit/clip)',
    speed: '🐌 45–75s',
    description: 'Veo 3.1 chất lượng cao nhất với độ sâu trường ảnh và ánh sáng chân thực tối đa.'
  },
  {
    id: 'google_veo_intro',
    name: 'Google Veo (Intro Video Generator)',
    badge: '🔴 Video AI Legacy',
    creditLabel: '🔴 Cao (~10–20 credit/clip)',
    speed: '🐌 30–60s',
    description: 'Sinh video chuyển động AI 4–8 giây cho cảnh mở đầu để giữ chân người xem (Legacy ID).'
  }
];

const GOOGLE_FLOW_MODELS = [...GOOGLE_FLOW_IMAGE_MODELS, ...GOOGLE_FLOW_VIDEO_MODELS];

const DEFAULT_IMAGE_GENERATION_SETTINGS = {
  provider: 'google_flow',
  model: 'nano_banana_pro',
  aspect_ratio: '16:9',
  output_count: 1,
  video_model: 'veo_3_1_lite',
  video_aspect_ratio: '16:9',
  video_output_count: 1,
  style_prompt: 'Cinematic documentary film still, 35mm photography, atmospheric natural lighting, realistic textures, cinematic composition, shallow depth of field, balanced color grading, high visual fidelity, 8k raw photo.',
  avoid_prompt: 'cartoon, anime, 3D CGI render, illustration, drawing, plastic skin, oversaturated, blown-out highlights, deformed hands, extra fingers, missing limbs, duplicate faces, distorted anatomy, text, watermark, signature, logo, blurry, low resolution.',
  negative_prompt: 'cartoon, anime, 3D CGI render, illustration, drawing, plastic skin, oversaturated, blown-out highlights, deformed hands, extra fingers, missing limbs, duplicate faces, distorted anatomy, text, watermark, signature, logo, blurry, low resolution.',
  thumbnail_variant: 'with_text',
  scene_0_source: 'from_thumbnail_without_text',
  enable_intro_video: true,
  intro_scene_target_seconds: 8.0,
  intro_crop_watermark: true,
  scene_duration_min_seconds: 25,
  scene_duration_target_seconds: 30,
  scene_duration_max_seconds: 35
};

const DEFAULT_PUBLISHING_SETTINGS = {
  category_id: '',
  language: 'vi',
  made_for_kids: null,
  notify_subscribers: true,
  contains_synthetic_media: true,
  description_template: ''
};

const YOUTUBE_CATEGORIES = [
  { id: '22', name: '22 - Mọi người & Blog (People & Blogs - Mặc định)' },
  { id: '24', name: '24 - Giải trí (Entertainment)' },
  { id: '27', name: '27 - Giáo dục (Education)' },
  { id: '28', name: '28 - Khoa học & Công nghệ (Science & Technology)' },
  { id: '10', name: '10 - Âm nhạc (Music)' },
  { id: '20', name: '20 - Trò chơi (Gaming)' },
  { id: '1', name: '1 - Phim & Hoạt hình (Film & Animation)' },
  { id: '2', name: '2 - Ô tô & Xe cộ (Autos & Vehicles)' },
  { id: '15', name: '15 - Thú cưng & Động vật (Pets & Animals)' },
  { id: '17', name: '17 - Thể thao (Sports)' },
  { id: '19', name: '19 - Du lịch & Sự kiện (Travel & Events)' },
  { id: '23', name: '23 - Hài kịch (Comedy)' },
  { id: '25', name: '25 - Tin tức & Chính trị (News & Politics)' },
  { id: '26', name: '26 - Hướng dẫn & Phong cách (Howto & Style)' },
  { id: '29', name: '29 - Hoạt động xã hội & Phi lợi nhuận (Nonprofits & Activism)' }
];

const PIPELINE_STEPS = [
  {
    key: 'title',
    label: 'Tiêu đề (Title)',
    description: 'Tự động tạo các phương án tiêu đề hấp dẫn.'
  },
  {
    key: 'slug',
    label: 'URL Slug',
    description: 'Tự động tạo slug để đặt tên file MP4 khi render.'
  },
  {
    key: 'description',
    label: 'Mô tả tóm tắt (Description)',
    description: 'Tự động tạo đoạn mô tả video chuẩn SEO.'
  },
  {
    key: 'tags',
    label: 'Tags & Hashtags',
    description: 'Tự động tạo tags và hashtags liên quan.'
  },
  {
    key: 'pinned_comment',
    label: 'Bình luận ghim',
    description: 'Tự động tạo nội dung bình luận ghim tương tác.'
  },
  {
    key: 'quiz',
    label: 'Quiz tương tác',
    description: 'Tự động tạo câu hỏi trắc nghiệm tương tác cho khán giả.'
  },
  {
    key: 'chapters',
    label: 'Chapters',
    description: 'Tự động tạo các mốc chapter sau khi kịch bản lõi hoàn tất.'
  },
  {
    key: 'thumbnail_with_text',
    label: 'Thumbnail có chữ',
    description: 'Tự động gửi prompt và lấy ảnh thumbnail có chữ.'
  },
  {
    key: 'thumbnail_without_text',
    label: 'Thumbnail không chữ',
    description: 'Tự động gửi prompt và lấy ảnh thumbnail không chữ.'
  },
  {
    key: 'audio',
    label: 'Tự động tạo audio',
    description: 'Tự kiểm duyệt kịch bản và gửi đúng nhà cung cấp của giọng đã chọn.'
  },
  {
    key: 'video_render',
    label: 'Dựng MP4',
    description: 'Lập cảnh theo SRT, tạo ảnh Google Flow và dựng MP4 1080p.'
  },
  {
    key: 'youtube_upload',
    label: 'Upload Private',
    description: 'Upload video, thumbnail và phụ đề lên đúng kênh, luôn giữ Private.'
  },
  {
    key: 'youtube_schedule',
    label: 'Đặt lịch đăng',
    description: 'Giữ khung giờ hợp lệ của kênh và đặt lịch sau khi YouTube xử lý xong.'
  }
];

const PIPELINE_STEP_LABELS = Object.fromEntries(
  PIPELINE_STEPS.map(step => [step.key, step.label])
);

const PUBLISH_CONFIGURATION_LABELS = {
  default_youtube_channel_id: 'kênh YouTube mặc định',
  youtube_oauth: 'OAuth YouTube',
  gpm_profile_id: 'GPM Profile riêng',
  gpm_profile_exclusive: 'GPM Profile không dùng chung với kênh khác',
  gpm_proxy_info: 'proxy riêng của GPM Profile',
  publication_timezone: 'múi giờ đăng',
  publication_slots: 'khung giờ đăng',
  publication_daily_limit: 'giới hạn video mỗi ngày',
  publication_lead_minutes: 'khoảng an toàn trước giờ đăng',
  publication_paused: 'bỏ tạm dừng lịch đăng',
  public_upload_verified: 'xác minh quyền đặt lịch public',
  made_for_kids: 'lựa chọn dành cho trẻ em'
};

function pipelineDependencies(thumbnailVariant) {
  return {
    video_render: ['audio'],
    youtube_upload: [
      'video_render',
      'title',
      'description',
      'tags',
      thumbnailVariant === 'with_text'
        ? 'thumbnail_with_text'
        : 'thumbnail_without_text'
    ],
    youtube_schedule: ['youtube_upload']
  };
}

function resolvePipelineDependencies(pipeline, thumbnailVariant) {
  const resolved = { ...DEFAULT_PIPELINE, ...pipeline };
  const enabled = [];
  const dependencies = pipelineDependencies(thumbnailVariant);
  let changed = true;
  while (changed) {
    changed = false;
    Object.entries(dependencies).forEach(([step, requiredSteps]) => {
      if (!resolved[step]) return;
      requiredSteps.forEach(requiredStep => {
        if (resolved[requiredStep]) return;
        resolved[requiredStep] = true;
        enabled.push(requiredStep);
        changed = true;
      });
    });
  }
  return { pipeline: resolved, enabled: [...new Set(enabled)] };
}

function pipelineDependents(pipeline, stepKey, thumbnailVariant) {
  const dependencies = pipelineDependencies(thumbnailVariant);
  const dependents = [];
  let frontier = [stepKey];
  while (frontier.length) {
    const source = frontier.shift();
    Object.entries(dependencies).forEach(([step, requiredSteps]) => {
      if (!pipeline[step] || dependents.includes(step) || !requiredSteps.includes(source)) return;
      dependents.push(step);
      frontier.push(step);
    });
  }
  return dependents;
}

function pipelineOutcome(pipeline) {
  if (pipeline.youtube_schedule) return 'Dựng, upload và đặt lịch';
  if (pipeline.youtube_upload) return 'Upload và giữ Private';
  if (pipeline.video_render) return 'Tạo MP4, không upload';
  if (pipeline.audio) return 'Kết thúc ở Audio';
  return 'Kết thúc sau khi tạo nội dung đã chọn';
}

function ProviderVoiceOptions({ voices }) {
  const providerNames = { genmax: 'Genmax', omnivoice: 'OmniVoice' };
  const groups = voices.reduce((result, voice) => {
    const providerId = voice.provider_id || 'genmax';
    if (!result[providerId]) result[providerId] = [];
    result[providerId].push(voice);
    return result;
  }, {});
  return Object.entries(groups).map(([providerId, items]) => (
    <optgroup key={providerId} label={providerNames[providerId] || providerId}>
      {items.map(voice => (
        <option key={voice.id} value={voice.id}>
          [{providerNames[providerId] || providerId}] {voice.name}
        </option>
      ))}
    </optgroup>
  ));
}

export default function Settings({
  lockedPromptVersion = '',
  chatGptOperation = ''
}) {
  const [promptsData, setPromptsData] = useState(null);
  const [voicesData, setVoicesData] = useState(null);
  const [youtubeChannels, setYoutubeChannels] = useState([]);
  const [pipelineNotice, setPipelineNotice] = useState('');
  const [activeVersion, setActiveVersion] = useState('');
  const [loadingMsg, setLoadingMsg] = useState('');
  const [resultMsg, setResultMsg] = useState('');
  const [resultSection, setResultSection] = useState('');
  const [savingSection, setSavingSection] = useState('');
  const [promptAssets, setPromptAssets] = useState([]);
  const [promptAssetsFolder, setPromptAssetsFolder] = useState('');
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [uploadingAsset, setUploadingAsset] = useState(false);
  const [assetMessage, setAssetMessage] = useState('');

  const fetchPromptAssets = async (version) => {
    if (!version) return;
    setLoadingAssets(true);
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(version)}/assets`
      );
      if (response.ok) {
        const data = await response.json();
        setPromptAssets(data.assets || []);
        setPromptAssetsFolder(data.folder_path || '');
      }
    } catch (err) {
      console.error('Lỗi khi lấy danh sách assets của prompt:', err);
    } finally {
      setLoadingAssets(false);
    }
  };

  useEffect(() => {
    if (activeVersion) {
      fetchPromptAssets(activeVersion);
    }
  }, [activeVersion]);

  const handleOpenAssetsFolder = async () => {
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(activeVersion)}/open-folder`,
        { method: 'POST' }
      );
      const data = await response.json();
      if (data.success) {
        setAssetMessage('Đã mở thư mục trên máy tính.');
        setTimeout(() => setAssetMessage(''), 4000);
      } else {
        setAssetMessage('Không thể mở thư mục.');
        setTimeout(() => setAssetMessage(''), 4000);
      }
    } catch (err) {
      console.error('Lỗi khi mở thư mục assets:', err);
      setAssetMessage('Lỗi khi mở thư mục.');
    }
  };

  const handleUploadAsset = async (event) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;
    setUploadingAsset(true);
    setAssetMessage('Đang tải ảnh lên...');
    try {
      for (let i = 0; i < files.length; i++) {
        const formData = new FormData();
        formData.append('file', files[i]);
        await fetch(
          `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(activeVersion)}/assets/upload`,
          {
            method: 'POST',
            body: formData
          }
        );
      }
      await fetchPromptAssets(activeVersion);
      setAssetMessage(`Đã tải lên thành công ${files.length} ảnh.`);
      setTimeout(() => setAssetMessage(''), 4000);
    } catch (err) {
      console.error('Lỗi khi tải ảnh lên:', err);
      setAssetMessage('Lỗi khi tải ảnh lên.');
    } finally {
      setUploadingAsset(false);
      event.target.value = '';
    }
  };

  const handleDeleteAsset = async (filename) => {
    if (!window.confirm(`Bạn có chắc muốn xóa ảnh "${filename}" khỏi bộ prompt này?`)) return;
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(activeVersion)}/assets/${encodeURIComponent(filename)}`,
        { method: 'DELETE' }
      );
      if (response.ok) {
        await fetchPromptAssets(activeVersion);
        setAssetMessage(`Đã xóa ảnh "${filename}".`);
        setTimeout(() => setAssetMessage(''), 3000);
      }
    } catch (err) {
      console.error('Lỗi khi xóa ảnh:', err);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      const [promptsResponse, voicesResponse, channelsResponse] = await Promise.all([
        fetch('http://127.0.0.1:8080/api/prompts'),
        fetch('http://127.0.0.1:8080/api/voices'),
        fetch('http://127.0.0.1:8080/api/youtube-comments/channels')
      ]);
      const prompts = await promptsResponse.json();
      const voices = await voicesResponse.json();
      const channels = await channelsResponse.json();
      setPromptsData(prompts);
      setVoicesData(voices);
      setYoutubeChannels(Array.isArray(channels.items) ? channels.items : []);
      setActiveVersion(prompts.active_version);
    } catch (err) {
      console.error("Lỗi khi lấy prompts:", err);
    }
  };

  const handlePromptChange = (key, value) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          prompts: {
            ...prev.versions[activeVersion].prompts,
            [key]: value
          }
        }
      }
    }));
  };

  const handleVersionNameChange = (value) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          name: value
        }
      }
    }));
  };

  const handleProjectUrlChange = (value) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          project_url: value
        }
      }
    }));
  };

  const handlePromptDefaultVoiceChange = (voiceId) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          default_voice_id: voiceId
        }
      }
    }));
  };

  const handlePromptDefaultYoutubeChannelChange = (channelId) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          default_youtube_channel_id: channelId
        }
      }
    }));
  };

  const handlePipelineChange = (stepKey, enabled) => {
    const version = promptsData.versions[activeVersion];
    const imageSettings = {
      ...DEFAULT_IMAGE_GENERATION_SETTINGS,
      ...version.image_generation_settings
    };
    const current = { ...DEFAULT_PIPELINE, ...version.pipeline };
    let nextPipeline = { ...current, [stepKey]: enabled };
    if (enabled) {
      const resolved = resolvePipelineDependencies(
        nextPipeline,
        imageSettings.thumbnail_variant
      );
      nextPipeline = resolved.pipeline;
      setPipelineNotice(
        resolved.enabled.length
          ? `Đã tự bật: ${resolved.enabled.map(key => PIPELINE_STEP_LABELS[key]).join(', ')}.`
          : ''
      );
    } else {
      const dependents = pipelineDependents(
        current,
        stepKey,
        imageSettings.thumbnail_variant
      );
      if (dependents.length) {
        const labels = dependents.map(key => PIPELINE_STEP_LABELS[key]).join(', ');
        if (!window.confirm(`Tắt bước này cũng sẽ tắt: ${labels}. Tiếp tục?`)) return;
        dependents.forEach(key => { nextPipeline[key] = false; });
        setPipelineNotice(`Đã tắt dây chuyền: ${labels}.`);
      } else {
        setPipelineNotice('');
      }
    }
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          pipeline: nextPipeline
        }
      }
    }));
  };

  const handlePromptSettingChange = (section, field, value) => {
    setPromptsData(prev => {
      const currentSection = {
        ...(section === 'image_generation_settings'
          ? DEFAULT_IMAGE_GENERATION_SETTINGS
          : DEFAULT_PUBLISHING_SETTINGS),
        ...prev.versions[activeVersion][section],
        [field]: value
      };
      if (section === 'image_generation_settings') {
        if (field === 'negative_prompt') {
          currentSection.avoid_prompt = value;
        } else if (field === 'avoid_prompt') {
          currentSection.negative_prompt = value;
        }
      }
      return {
        ...prev,
        versions: {
          ...prev.versions,
          [activeVersion]: {
            ...prev.versions[activeVersion],
            [section]: currentSection
          }
        }
      };
    });
  };

  const handleThumbnailVariantChange = (thumbnailVariant) => {
    const version = promptsData.versions[activeVersion];
    const current = { ...DEFAULT_PIPELINE, ...version.pipeline };
    const resolved = resolvePipelineDependencies(current, thumbnailVariant);
    handlePromptSettingChange('image_generation_settings', 'thumbnail_variant', thumbnailVariant);
    if (current.youtube_upload) {
      setPromptsData(prev => ({
        ...prev,
        versions: {
          ...prev.versions,
          [activeVersion]: {
            ...prev.versions[activeVersion],
            pipeline: resolved.pipeline
          }
        }
      }));
      setPipelineNotice(
        resolved.enabled.length
          ? `Đã tự bật thumbnail được chọn: ${resolved.enabled.map(key => PIPELINE_STEP_LABELS[key]).join(', ')}.`
          : ''
      );
    }
  };

  const handleVersionChange = (e) => {
    const newVersion = e.target.value;
    setActiveVersion(newVersion);
    setPromptsData(prev => ({ ...prev, active_version: newVersion }));
  };

  const handleDuplicateVersion = () => {
    const newName = prompt("Nhập tên cho phiên bản mới:");
    if (!newName) return;
    
    const versionId = "v_" + Date.now();
    
    setPromptsData(prev => {
      const newData = { ...prev };
      const currentPrompts = { ...newData.versions[activeVersion].prompts };
      
      newData.versions[versionId] = {
        name: newName,
        project_url: newData.versions[activeVersion].project_url,
        default_voice_id:
          newData.versions[activeVersion].default_voice_id || '',
        default_youtube_channel_id:
          newData.versions[activeVersion].default_youtube_channel_id || '',
        pipeline: {
          ...DEFAULT_PIPELINE,
          ...(newData.versions[activeVersion].pipeline || {})
        },
        image_generation_settings: {
          ...DEFAULT_IMAGE_GENERATION_SETTINGS,
          ...(newData.versions[activeVersion].image_generation_settings || {})
        },
        publishing_settings: {
          ...DEFAULT_PUBLISHING_SETTINGS,
          ...(newData.versions[activeVersion].publishing_settings || {})
        },
        prompts: currentPrompts
      };
      
      newData.active_version = versionId;
      setActiveVersion(versionId);
      return newData;
    });
  };

  const handleDeleteVersion = () => {
    if (activeVersion === lockedPromptVersion) {
      alert('Bộ prompt này đang được job sử dụng nên chưa thể xóa.');
      return;
    }
    if (Object.keys(promptsData.versions).length <= 1) {
      alert("Không thể xóa phiên bản duy nhất!");
      return;
    }
    
    if (!confirm("Bạn có chắc muốn xóa phiên bản này?")) return;
    
    setPromptsData(prev => {
      const newData = { ...prev };
      delete newData.versions[activeVersion];
      
      const remainingVersions = Object.keys(newData.versions);
      const nextVersion = remainingVersions[0];
      
      newData.active_version = nextVersion;
      setActiveVersion(nextVersion);
      return newData;
    });
  };

  const saveSection = async (sectionKey, loadingText, successText, request) => {
    try {
      setSavingSection(sectionKey);
      setLoadingMsg(loadingText);
      setResultMsg('');
      setResultSection(sectionKey);
      const response = await request();
      const contentType = response.headers.get('content-type') || '';
      const result = contentType.includes('application/json')
        ? await response.json()
        : { detail: await response.text() };
      if (!response.ok) {
        const compatibilityHint = response.status === 404
          ? ' Backend chưa nạp phiên bản API mới; hãy khởi động lại hệ thống.'
          : '';
        throw new Error(
          (result.detail || `Không thể lưu thiết lập (${response.status}).`) +
          compatibilityHint
        );
      }
      setResultMsg(`✅ ${successText}`);
      return result;
    } catch (err) {
      setResultMsg('❌ ' + err.message);
      return null;
    } finally {
      setSavingSection('');
      setLoadingMsg('');
    }
  };

  const handleSaveVersionName = async () => {
    const versionId = activeVersion;
    const name = promptsData.versions[versionId].name;
    const result = await saveSection(
      'version-name',
      'Đang lưu tên bộ prompt...',
      'Đã lưu tên bộ prompt.',
      () => fetch(`http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/name`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      })
    );
    if (result && activeVersion === versionId) {
      setPromptsData(prev => ({
        ...prev,
        versions: {
          ...prev.versions,
          [versionId]: {
            ...prev.versions[versionId],
            name: result.version.name
          }
        }
      }));
    }
  };

  const handleSaveProject = () => {
    const versionId = activeVersion;
    const projectUrl = promptsData.versions[versionId].project_url;
    return saveSection(
      'project',
      'Đang lưu ChatGPT Project...',
      'Đã lưu ChatGPT Project cho bộ prompt này.',
      () => fetch(`http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/project`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_url: projectUrl })
      })
    );
  };

  const handleSavePromptDefaultVoice = () => {
    const versionId = activeVersion;
    const voiceId = promptsData.versions[versionId].default_voice_id || '';
    return saveSection(
      'prompt-default-voice',
      'Đang lưu giọng mặc định của bộ prompt...',
      'Đã lưu giọng mặc định của bộ prompt.',
      () => fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/default-voice`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ voice_id: voiceId })
        }
      )
    );
  };

  const handleSavePromptDefaultYoutubeChannel = async () => {
    const versionId = activeVersion;
    const channelId = (
      promptsData.versions[versionId].default_youtube_channel_id || ''
    );
    const result = await saveSection(
      'prompt-default-youtube-channel',
      'Đang lưu kênh YouTube mặc định của bộ prompt...',
      'Đã lưu kênh YouTube mặc định của bộ prompt.',
      () => fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/default-youtube-channel`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ channel_id: channelId })
        }
      )
    );
    if (result && activeVersion === versionId) {
      setPromptsData(prev => ({
        ...prev,
        versions: {
          ...prev.versions,
          [versionId]: result.version
        }
      }));
    }
  };

  const handleSavePipeline = async () => {
    const versionId = activeVersion;
    const pipeline = {
      ...DEFAULT_PIPELINE,
      ...promptsData.versions[versionId].pipeline
    };
    const result = await saveSection(
      'pipeline',
      'Đang lưu pipeline của bộ prompt...',
      'Đã lưu pipeline cho bộ prompt này.',
      () => fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/pipeline`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(pipeline)
        }
      )
    );
    if (result && activeVersion === versionId) {
      setPromptsData(prev => ({
        ...prev,
        versions: {
          ...prev.versions,
          [versionId]: {
            ...prev.versions[versionId],
            pipeline: result.pipeline
          }
        }
      }));
      const notices = [];
      if (Array.isArray(result.auto_enabled) && result.auto_enabled.length) {
        notices.push(
          `Backend đã tự bật: ${result.auto_enabled.map(key => PIPELINE_STEP_LABELS[key]).join(', ')}.`
        );
      }
      if (result.ready === false) {
        const missing = (result.missing_configuration || [])
          .map(key => PUBLISH_CONFIGURATION_LABELS[key] || key)
          .join(', ');
        notices.push(`Chưa sẵn sàng chạy tự động: ${missing}.`);
      } else if (result.pipeline?.youtube_upload) {
        notices.push('Cấu hình upload/đặt lịch đã sẵn sàng; artifact sẽ được kiểm tra lại khi job chạy.');
      }
      setPipelineNotice(notices.join(' '));
    }
  };

  const handleSaveImageGeneration = async () => {
    const versionId = activeVersion;
    const currentImg = promptsData.versions[versionId]?.image_generation_settings || {};
    const negPrompt = currentImg.negative_prompt ?? currentImg.avoid_prompt ?? '';
    const settings = {
      ...DEFAULT_IMAGE_GENERATION_SETTINGS,
      ...currentImg,
      negative_prompt: negPrompt,
      avoid_prompt: negPrompt
    };
    const result = await saveSection(
      'image-generation',
      'Đang lưu cấu hình tạo ảnh và thumbnail...',
      'Đã lưu cấu hình tạo ảnh và thumbnail.',
      () => fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/image-generation`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(settings)
        }
      )
    );
    if (result?.version && activeVersion === versionId) {
      setPromptsData(prev => ({
        ...prev,
        versions: { ...prev.versions, [versionId]: result.version }
      }));
    }
  };

  const handleSavePublishing = async () => {
    const versionId = activeVersion;
    const currentImg = promptsData.versions[versionId]?.image_generation_settings || {};
    const negPrompt = currentImg.negative_prompt ?? currentImg.avoid_prompt ?? '';
    const imgSettings = {
      ...DEFAULT_IMAGE_GENERATION_SETTINGS,
      ...currentImg,
      negative_prompt: negPrompt,
      avoid_prompt: negPrompt
    };
    const settings = {
      ...DEFAULT_PUBLISHING_SETTINGS,
      ...promptsData.versions[versionId].publishing_settings,
      contains_synthetic_media: true
    };
    const result = await saveSection(
      'publishing',
      'Đang lưu cấu hình đăng YouTube...',
      'Đã lưu cấu hình đăng YouTube.',
      async () => {
        await fetch(
          `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/image-generation`,
          {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(imgSettings)
          }
        );
        return fetch(
          `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/publishing`,
          {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
          }
        );
      }
    );
    if (result?.version && activeVersion === versionId) {
      setPromptsData(prev => ({
        ...prev,
        versions: { ...prev.versions, [versionId]: result.version }
      }));
    }
  };

  const handleSavePrompt = (promptKey, promptLabel) => {
    const versionId = activeVersion;
    const value = promptsData.versions[versionId].prompts[promptKey] || '';
    return saveSection(
      `prompt-${promptKey}`,
      `Đang lưu ${promptLabel}...`,
      `Đã lưu ${promptLabel}.`,
      async () => {
        const res = await fetch(
          `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/fields/${encodeURIComponent(promptKey)}`,
          {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ value })
          }
        );
        if (!res.ok) throw new Error("Failed to save text");

        if (promptKey === 'thumb_text' || promptKey === 'thumb_notext') {
          const imageKey = `${promptKey}_image_base64`;
          const imageVal = promptsData.versions[versionId].prompts[imageKey] || '';
          const res2 = await fetch(
            `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/fields/${encodeURIComponent(imageKey)}`,
            {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ value: imageVal })
            }
          );
          if (!res2.ok) throw new Error("Failed to save image");
        }
        return res;
      }
    );
  };

  const handleSave = async () => {
    try {
      setSavingSection('all');
      setLoadingMsg('Đang lưu thiết lập...');
      setResultMsg('');

      const promptsResponse = await fetch('http://127.0.0.1:8080/api/prompts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(promptsData)
      });
      if (!promptsResponse.ok) {
        const promptsResult = await promptsResponse.json();
        throw new Error(promptsResult.detail || 'Không thể lưu cấu hình prompt.');
      }
      setResultMsg(
        lockedPromptVersion
          ? '✅ Đã lưu các thiết lập khác. Bộ prompt đang chạy được giữ nguyên.'
          : '✅ Đã lưu cấu hình Prompt và trình duyệt thành công!'
      );
    } catch (err) {
      setResultMsg('❌ ' + err.message);
    } finally {
      setSavingSection('');
      setLoadingMsg('');
    }
  };

  if (!promptsData || !voicesData || !promptsData.versions[activeVersion]) {
    return <div style={{padding: '20px', color: 'white'}}>Loading Settings...</div>;
  }

  const currentVersion = promptsData.versions[activeVersion];
  const activeVersionLocked = Boolean(
    lockedPromptVersion && activeVersion === lockedPromptVersion
  );
  const lockedVersionName = lockedPromptVersion
    ? promptsData.versions[lockedPromptVersion]?.name || lockedPromptVersion
    : '';
  const globalDefaultVoice = voicesData.voices.find(
    voice => voice.id === voicesData.active_voice_id
  );
  const promptDefaultVoiceId = currentVersion.default_voice_id || '';
  const promptDefaultVoiceMissing = Boolean(
    promptDefaultVoiceId &&
    !voicesData.voices.some(voice => voice.id === promptDefaultVoiceId)
  );
  const promptDefaultYoutubeChannelId = (
    currentVersion.default_youtube_channel_id || ''
  );
  const promptDefaultYoutubeChannelMissing = Boolean(
    promptDefaultYoutubeChannelId &&
    !youtubeChannels.some(
      channel => channel.channel_id === promptDefaultYoutubeChannelId
    )
  );
  const currentPipeline = {
    ...DEFAULT_PIPELINE,
    ...currentVersion.pipeline
  };
  const currentImageGeneration = {
    ...DEFAULT_IMAGE_GENERATION_SETTINGS,
    ...currentVersion.image_generation_settings,
    negative_prompt: (
      currentVersion.image_generation_settings?.negative_prompt ??
      currentVersion.image_generation_settings?.avoid_prompt ??
      ''
    ),
    avoid_prompt: (
      currentVersion.image_generation_settings?.avoid_prompt ??
      currentVersion.image_generation_settings?.negative_prompt ??
      ''
    )
  };
  const currentPublishing = {
    ...DEFAULT_PUBLISHING_SETTINGS,
    ...currentVersion.publishing_settings
  };

  const promptFields = [
    { key: 'outline', label: '1. Dàn ý (Outline)', help: 'Biến có sẵn: {transcript}' },
    { key: 'intro', label: '2. Mở đầu (Intro)' },
    { key: 'body', label: '3. Nội dung chính (Body)', help: 'Biến có sẵn: {part}' },
    { key: 'outro', label: '4. Kết thúc (Outro)' },
    { key: 'title', label: '5. Tiêu đề (Title)' },
    { key: 'slug', label: '6. URL Slug (Dùng đặt tên file render MP4)' },
    { key: 'description', label: '7. Mô tả video (Description)' },
    { key: 'tags', label: '8. Tags & Hashtags' },
    { key: 'pinned_comment', label: '9. Bình luận ghim' },
    { key: 'quiz', label: '10. Quiz tương tác' },
    { key: 'chapters', label: '11. Phân đoạn (Chapters)' },
    { key: 'thumb_text', label: '12. Thumbnail (Có chữ)' },
    { key: 'thumb_notext', label: '13. Thumbnail (Không chữ)' },
  ];


  const handleImageUpload = (e, imageKey) => {
    const files = Array.from(e.target.files);
    if (!files.length) return;

    let currentArray = [];
    try {
      const existing = currentVersion.prompts[imageKey];
      if (existing) {
        if (existing.startsWith('[')) {
          currentArray = JSON.parse(existing);
        } else {
          currentArray = [existing];
        }
      }
    } catch {}

    let processedCount = 0;
    const newBase64s = [];

    files.forEach(file => {
      const reader = new FileReader();
      reader.onload = (event) => {
        const base64 = event.target.result.split(',')[1];
        newBase64s.push(base64);
        processedCount++;

        if (processedCount === files.length) {
          const finalArray = [...currentArray, ...newBase64s];
          handlePromptChange(imageKey, JSON.stringify(finalArray));
        }
      };
      reader.readAsDataURL(file);
    });
  };

  const handleRemoveImage = (imageKey, indexToRemove) => {
    let currentArray = [];
    try {
      const existing = currentVersion.prompts[imageKey];
      if (existing) {
        if (existing.startsWith('[')) {
          currentArray = JSON.parse(existing);
        } else {
          currentArray = [existing];
        }
      }
    } catch {}

    if (indexToRemove === -1) {
      handlePromptChange(imageKey, '');
    } else {
      currentArray.splice(indexToRemove, 1);
      if (currentArray.length === 0) {
        handlePromptChange(imageKey, '');
      } else {
        handlePromptChange(imageKey, JSON.stringify(currentArray));
      }
    }
  };

  const renderImagePreviews = (fieldKey) => {
    const imageKey = `${fieldKey}_image_base64`;
    const existing = currentVersion.prompts[imageKey];
    let images = [];
    if (existing) {
      if (existing.startsWith('[')) {
        try {
          images = JSON.parse(existing);
        } catch {
          images = [existing];
        }
      } else {
        images = [existing];
      }
    }

    if (images.length === 0) {
      return (
        <label style={{ cursor: activeVersionLocked ? 'not-allowed' : 'pointer', textAlign: 'center', color: '#888', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '5px', opacity: activeVersionLocked ? 0.5 : 1, margin: 0 }}>
          <span style={{ fontSize: '2em' }}>🖼️</span>
          <span style={{ fontSize: '0.8em' }}>Tải ảnh mẫu lên</span>
          <input type="file" multiple accept="image/png, image/jpeg, image/webp" style={{ display: 'none' }} disabled={activeVersionLocked} onChange={(e) => handleImageUpload(e, imageKey)} />
        </label>
      );
    }

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', width: '100%' }}>
        <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap', justifyContent: 'center' }}>
          {images.map((b64, idx) => (
            <div key={idx} style={{ position: 'relative' }}>
              <img
                src={`data:image/png;base64,${b64}`}
                alt="Reference"
                style={{ width: '45px', height: '45px', objectFit: 'cover', borderRadius: '4px', border: '1px solid #444' }}
              />
              <button
                onClick={() => handleRemoveImage(imageKey, idx)}
                disabled={activeVersionLocked}
                style={{ position: 'absolute', top: '-5px', right: '-5px', background: '#e74c3c', border: 'none', color: 'white', borderRadius: '50%', width: '16px', height: '16px', fontSize: '10px', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: activeVersionLocked ? 'not-allowed' : 'pointer', padding: 0 }}
                title="Xóa ảnh này"
              >
                ✕
              </button>
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', gap: '5px', justifyContent: 'center', marginTop: '4px' }}>
          <label style={{ cursor: activeVersionLocked ? 'not-allowed' : 'pointer', background: 'var(--accent)', color: 'white', padding: '2px 8px', borderRadius: '4px', fontSize: '0.7em', opacity: activeVersionLocked ? 0.5 : 1, margin: 0 }}>
            + Thêm
            <input type="file" multiple accept="image/png, image/jpeg, image/webp" style={{ display: 'none' }} disabled={activeVersionLocked} onChange={(e) => handleImageUpload(e, imageKey)} />
          </label>
          <button onClick={() => handleRemoveImage(imageKey, -1)} disabled={activeVersionLocked} style={{ background: '#e74c3c', border: 'none', color: 'white', padding: '2px 8px', borderRadius: '4px', fontSize: '0.7em', cursor: activeVersionLocked ? 'not-allowed' : 'pointer', opacity: activeVersionLocked ? 0.5 : 1, margin: 0 }}>
            Xóa hết
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="settings-container">
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
        <div>
          <h1 className="hero-title">Prompt Management</h1>
          <p className="hero-subtitle">Quản lý và chỉnh sửa các lệnh AI hệ thống sử dụng.</p>
        </div>
        <button
          className="btn-save btn-large"
          onClick={handleSave}
          disabled={Boolean(savingSection)}
          style={{padding: '15px 30px', fontSize: '1.1em'}}
        >
          💾 Lưu Tất Cả
        </button>
      </div>

      {lockedPromptVersion && (
        <div className="prompt-lock-notice" role="status">
          🔒 Job {chatGptOperation || 'ChatGPT'} đang dùng bộ prompt
          <strong> {lockedVersionName}</strong>. Chỉ bộ này tạm khóa; bạn vẫn có
          thể quản lý các bộ prompt khác và danh sách giọng đọc.
        </div>
      )}

      <div className="version-control">
        <div className="version-editor">
          <label style={{color: '#fff', fontWeight: 'bold'}}>Phiên bản hiện tại:</label>
          <select value={activeVersion} onChange={handleVersionChange} className="version-select">
            {Object.entries(promptsData.versions).map(([key, version]) => (
              <option key={key} value={key}>{version.name}</option>
            ))}
          </select>
          <input
            value={currentVersion.name}
            onChange={(event) => handleVersionNameChange(event.target.value)}
            placeholder="Tên bộ prompt"
            className="version-select version-name-input"
            maxLength={100}
            disabled={activeVersionLocked}
          />
          <button
            className="btn-save section-save-button"
            onClick={handleSaveVersionName}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu tên
          </button>
        </div>
        
        <div style={{display: 'flex', gap: '10px'}}>
          <button className="btn-secondary" onClick={handleDuplicateVersion}>➕ Tạo Bản Sao</button>
          <button
            className="btn-danger"
            onClick={handleDeleteVersion}
            disabled={activeVersionLocked}
            title={
              activeVersionLocked
                ? 'Bộ prompt này đang được job sử dụng'
                : 'Xóa bộ prompt hiện tại'
            }
          >
            🗑️ Xóa Bản Này
          </button>
        </div>
      </div>

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>🎙️ Giọng mặc định của bộ prompt</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Video Fetcher sẽ tự chọn giọng này khi bạn chọn bộ prompt.
              Bạn vẫn có thể đổi giọng thủ công trước khi tạo từng video.
            </div>
          </div>
          <button
            className="btn-save section-save-button"
            onClick={handleSavePromptDefaultVoice}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu
          </button>
        </div>
        <select
          value={promptDefaultVoiceId}
          onChange={(event) => handlePromptDefaultVoiceChange(event.target.value)}
          className="version-select"
          style={{ width: '100%', marginTop: '12px' }}
          disabled={activeVersionLocked}
        >
          <option value="">
            Dùng giọng mặc định chung
            {globalDefaultVoice ? ` — ${globalDefaultVoice.name}` : ''}
          </option>
          {promptDefaultVoiceMissing && (
            <option value={promptDefaultVoiceId}>
              ⚠️ Giọng đã bị xóa — sẽ dùng giọng mặc định chung
            </option>
          )}
          <ProviderVoiceOptions voices={voicesData.voices} />
        </select>
      </div>

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>🌐 ChatGPT Project viết kịch bản</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Mỗi bộ prompt có thể lưu kịch bản vào một ChatGPT Project riêng.
            </div>
          </div>
          <button
            className="btn-save section-save-button"
            onClick={handleSaveProject}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu
          </button>
        </div>
        <input
          value={currentVersion.project_url || ''}
          onChange={(event) => handleProjectUrlChange(event.target.value)}
          placeholder="https://chatgpt.com/g/g-p-.../project"
          className="version-select"
          style={{ width: '100%', marginTop: '12px' }}
          disabled={activeVersionLocked}
        />
      </div>

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>📺 Kênh YouTube mặc định của bộ prompt</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Mọi video cũ và mới thuộc bộ prompt này sẽ tự chọn kênh này khi
              gắn link đã đăng và đồng bộ bình luận.
            </div>
          </div>
          <button
            className="btn-save section-save-button"
            onClick={handleSavePromptDefaultYoutubeChannel}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu
          </button>
        </div>
        <select
          value={promptDefaultYoutubeChannelId}
          onChange={(event) => handlePromptDefaultYoutubeChannelChange(event.target.value)}
          className="version-select"
          style={{ width: '100%', marginTop: '12px' }}
          disabled={activeVersionLocked}
        >
          <option value="">Chưa chọn kênh mặc định</option>
          {promptDefaultYoutubeChannelMissing && (
            <option value={promptDefaultYoutubeChannelId}>
              ⚠️ Kênh đã ngắt kết nối — hãy chọn lại
            </option>
          )}
          {youtubeChannels.map(channel => (
            <option key={channel.channel_id} value={channel.channel_id}>
              {channel.title}
            </option>
          ))}
        </select>
        {!youtubeChannels.length && (
          <div className="help-text" style={{ marginTop: 8, color: '#f5b041' }}>
            Chưa có kênh YouTube nào được kết nối. Hãy kết nối kênh trong menu Channel Hub.
          </div>
        )}
        {resultSection === 'prompt-default-youtube-channel' &&
          (loadingMsg || resultMsg) && (
            <div className="status-box inline-save-status" role="status">
              {loadingMsg && <p className="loading">{loadingMsg}</p>}
              {resultMsg && <p className="result">{resultMsg}</p>}
            </div>
          )}
      </div>

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>⚙️ Pipeline tự động của bộ prompt</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Bốn bước lõi Dàn ý → Intro → Body → Outro luôn bắt buộc. Các bước
              dưới đây được áp dụng độc lập cho video mới của riêng bộ prompt này.
            </div>
          </div>
          <button
            className="btn-save section-save-button"
            onClick={handleSavePipeline}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu pipeline
          </button>
        </div>
        <div className="pipeline-core-flow" aria-label="Các bước lõi bắt buộc">
          <span>Dàn ý</span><b>→</b><span>Intro</span><b>→</b><span>Body</span><b>→</b><span>Outro</span>
        </div>
        <div className="pipeline-options">
          {PIPELINE_STEPS.map((step, index) => (
            <label key={step.key} className="pipeline-option">
              <input
                type="checkbox"
                checked={currentPipeline[step.key]}
                onChange={(event) => handlePipelineChange(step.key, event.target.checked)}
                disabled={activeVersionLocked}
              />
              <span className="pipeline-step-number">{index + 6}</span>
              <span>
                <strong>{step.label}</strong>
                <small>{step.description}</small>
              </span>
            </label>
          ))}
        </div>
        <div className="pipeline-outcome" role="status">
          Kết quả: <strong>{pipelineOutcome(currentPipeline)}</strong>
        </div>
        {pipelineNotice && (
          <div className="help-text" style={{
            marginTop: 8,
            color: pipelineNotice.includes('Chưa sẵn sàng') ? '#f5b041' : '#4dd0e1'
          }}>
            {pipelineNotice}
          </div>
        )}
        <div className="help-text pipeline-snapshot-help">
          Mỗi job lưu một bản chụp pipeline khi được thêm vào hàng đợi. Sửa cấu
          hình tại đây không thay đổi job đã xếp hàng hoặc đang phục hồi.
        </div>
      </div>

      {currentPipeline.video_render && (
        <div className="prompt-item" style={{ marginBottom: '20px' }}>
          <div className="prompt-header">
            <div>
              <label>🖼️ Cấu hình Media & Video Google Flow (Nano Banana 2 / Omni 1.1 / Veo 3.1)</label>
              <div className="help-text" style={{ marginTop: 5 }}>
                Hệ thống chia phân cảnh theo phụ đề SRT (25–35s), tạo ảnh/video chất lượng cao qua Google Flow và dựng thành video MP4 hoàn chỉnh.
              </div>
            </div>
            <button
              className="btn-save section-save-button"
              onClick={handleSaveImageGeneration}
              disabled={Boolean(savingSection) || activeVersionLocked}
            >
              💾 Lưu cấu hình media
            </button>
          </div>

          {/* Agent Settings Pro-tip Banner */}
          <div
            style={{
              background: 'linear-gradient(135deg, rgba(59, 130, 246, 0.12), rgba(16, 185, 129, 0.08))',
              border: '1px solid rgba(59, 130, 246, 0.25)',
              borderRadius: 8,
              padding: '12px 16px',
              marginBottom: 16,
              display: 'flex',
              alignItems: 'flex-start',
              gap: 12
            }}
          >
            <span style={{ fontSize: '1.2rem', lineHeight: 1 }}>💡</span>
            <div style={{ fontSize: '0.85rem', color: '#e2e8f0', lineHeight: 1.5 }}>
              <strong style={{ color: '#93c5fd' }}>Mẹo tự động hóa mượt mà:</strong> Trên tài khoản Google Flow, bạn hãy vào <em>Cài đặt tác nhân (Agent settings)</em> &rarr; mục <em>Xác nhận trước khi tạo</em> &rarr; chọn <strong>"Không bao giờ"</strong> (Tác nhân sẽ tự động tạo nội dung nghe nhìn và trừ tín dụng). Thao tác này giúp bot sinh ảnh/video liên tục mà không bị dừng chờ duyệt popup.
            </div>
          </div>

          {/* Section 1: Image Generation Configuration */}
          <div style={{ marginBottom: 18 }}>
            <h4 style={{ margin: '0 0 10px 0', fontSize: '0.95rem', color: '#6ee7b7', display: 'flex', alignItems: 'center', gap: 6 }}>
              <span>📸</span> 1. Cấu hình Tạo Hình Ảnh (Image Models)
            </h4>
            <div className="production-settings-grid" style={{ marginBottom: 10 }}>
              <label>
                Model tạo ảnh
                <select
                  className="version-select"
                  value={currentImageGeneration.model || 'nano_banana_2'}
                  onChange={event => handlePromptSettingChange(
                    'image_generation_settings', 'model', event.target.value
                  )}
                  disabled={activeVersionLocked}
                >
                  {GOOGLE_FLOW_IMAGE_MODELS.map(m => (
                    <option key={m.id} value={m.id}>
                      {m.name} [{m.badge}]
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Tỉ lệ khung hình (Aspect)
                <select
                  className="version-select"
                  value={currentImageGeneration.aspect_ratio || '16:9'}
                  onChange={event => handlePromptSettingChange(
                    'image_generation_settings', 'aspect_ratio', event.target.value
                  )}
                  disabled={activeVersionLocked}
                >
                  <option value="16:9">16:9 (YouTube Ngang)</option>
                  <option value="9:16">9:16 (Shorts / Dọc)</option>
                  <option value="1:1">1:1 (Vuông)</option>
                  <option value="4:3">4:3 (Truyền thống)</option>
                  <option value="3:4">3:4 (Dọc cổ điển)</option>
                </select>
              </label>

              <label>
                Số lượng ảnh / lần (Outputs)
                <select
                  className="version-select"
                  value={currentImageGeneration.output_count || 2}
                  onChange={event => handlePromptSettingChange(
                    'image_generation_settings', 'output_count', Number(event.target.value)
                  )}
                  disabled={activeVersionLocked}
                >
                  <option value="1">x1 (1 ảnh)</option>
                  <option value="2">x2 (2 ảnh - Mặc định Flow)</option>
                  <option value="3">x3 (3 ảnh)</option>
                  <option value="4">x4 (4 ảnh)</option>
                </select>
              </label>

              <label>
                Nguồn ảnh Scene 0 (Ảnh mở đầu)
                <select
                  className="version-select"
                  value={currentImageGeneration.scene_0_source || 'from_thumbnail_without_text'}
                  onChange={event => handlePromptSettingChange(
                    'image_generation_settings', 'scene_0_source', event.target.value
                  )}
                  disabled={activeVersionLocked}
                >
                  <option value="from_thumbnail_without_text">Từ Thumbnail không chữ (Đồng bộ ảnh bìa - Khuyên dùng)</option>
                  <option value="from_thumbnail_with_text">Từ Thumbnail có chữ</option>
                  <option value="from_intro_transcript">Theo kịch bản Intro (Không dùng Thumbnail)</option>
                </select>
              </label>

              {[
                ['scene_duration_min_seconds', 'Tối thiểu (giây)'],
                ['scene_duration_target_seconds', 'Mục tiêu (giây)'],
                ['scene_duration_max_seconds', 'Tối đa (giây)']
              ].map(([key, label]) => (
                <label key={key}>
                  {label}
                  <input
                    type="number"
                    min="10"
                    max="90"
                    value={currentImageGeneration[key]}
                    onChange={event => handlePromptSettingChange(
                      'image_generation_settings', key, Number(event.target.value)
                    )}
                    disabled={activeVersionLocked}
                  />
                </label>
              ))}
            </div>

            {/* Selected Image Model Info Card */}
            {(() => {
              const selectedImgModel = GOOGLE_FLOW_IMAGE_MODELS.find(
                m => m.id === (currentImageGeneration.model || 'nano_banana_2')
              ) || GOOGLE_FLOW_IMAGE_MODELS[0];
              return (
                <div
                  style={{
                    background: 'rgba(255, 255, 255, 0.03)',
                    border: '1px solid rgba(255, 255, 255, 0.08)',
                    borderRadius: 8,
                    padding: '8px 12px',
                    fontSize: '0.82rem'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                    <strong style={{ color: '#6ee7b7' }}>{selectedImgModel.name}</strong>
                    <span style={{ fontSize: '0.76rem', color: '#93c5fd' }}>({selectedImgModel.creditLabel} • {selectedImgModel.speed})</span>
                  </div>
                  <div style={{ color: '#cbd5e1', lineHeight: 1.4 }}>
                    {selectedImgModel.description}
                  </div>
                </div>
              );
            })()}
          </div>

          {/* Section 2: AI Video Intro Configuration */}
          <div style={{ marginBottom: 18, borderTop: '1px solid rgba(255, 255, 255, 0.08)', paddingTop: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
              <h4 style={{ margin: 0, fontSize: '0.95rem', color: '#a78bfa', display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>🎬</span> 2. Cấu hình Tạo Video Intro (AI Video Models)
              </h4>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: '0.88rem', color: '#f1f5f9' }}>
                <input
                  type="checkbox"
                  checked={currentImageGeneration.enable_intro_video !== false}
                  onChange={event => handlePromptSettingChange(
                    'image_generation_settings', 'enable_intro_video', event.target.checked
                  )}
                  disabled={activeVersionLocked}
                />
                Bật sinh Video AI cho cảnh mở đầu
              </label>
            </div>

            {currentImageGeneration.enable_intro_video !== false && (
              <>
                <div className="production-settings-grid" style={{ marginBottom: 10 }}>
                  <label>
                    Model Video Google Flow
                    <select
                      className="version-select"
                      value={currentImageGeneration.video_model || 'omni_1_1_flash'}
                      onChange={event => handlePromptSettingChange(
                        'image_generation_settings', 'video_model', event.target.value
                      )}
                      disabled={activeVersionLocked}
                    >
                      {GOOGLE_FLOW_VIDEO_MODELS.map(m => (
                        <option key={m.id} value={m.id}>
                          {m.name} [{m.badge}]
                        </option>
                      ))}
                    </select>
                  </label>

                  <label>
                    Tỉ lệ Video Intro
                    <select
                      className="version-select"
                      value={currentImageGeneration.video_aspect_ratio || '16:9'}
                      onChange={event => handlePromptSettingChange(
                        'image_generation_settings', 'video_aspect_ratio', event.target.value
                      )}
                      disabled={activeVersionLocked}
                    >
                      <option value="16:9">16:9 (Widescreen Ngang)</option>
                      <option value="9:16">9:16 (Vertical Dọc)</option>
                    </select>
                  </label>

                  <label>
                    Thời lượng Intro mục tiêu (giây)
                    <input
                      type="number"
                      min="4"
                      max="15"
                      step="0.5"
                      value={currentImageGeneration.intro_scene_target_seconds || 8.0}
                      onChange={event => handlePromptSettingChange(
                        'image_generation_settings', 'intro_scene_target_seconds', Number(event.target.value)
                      )}
                      disabled={activeVersionLocked}
                    />
                  </label>

                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 22, cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={currentImageGeneration.intro_crop_watermark !== false}
                      onChange={event => handlePromptSettingChange(
                        'image_generation_settings', 'intro_crop_watermark', event.target.checked
                      )}
                      disabled={activeVersionLocked}
                    />
                    <span>Cắt watermark SynthID</span>
                  </label>
                </div>

                {/* Selected Video Model Info Card */}
                {(() => {
                  const selectedVidModel = GOOGLE_FLOW_VIDEO_MODELS.find(
                    m => m.id === (currentImageGeneration.video_model || 'omni_1_1_flash')
                  ) || GOOGLE_FLOW_VIDEO_MODELS[0];
                  return (
                    <div
                      style={{
                        background: 'rgba(255, 255, 255, 0.03)',
                        border: '1px solid rgba(255, 255, 255, 0.08)',
                        borderRadius: 8,
                        padding: '8px 12px',
                        fontSize: '0.82rem'
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                        <strong style={{ color: '#a78bfa' }}>{selectedVidModel.name}</strong>
                        <span style={{ fontSize: '0.76rem', color: '#93c5fd' }}>({selectedVidModel.creditLabel} • {selectedVidModel.speed})</span>
                      </div>
                      <div style={{ color: '#cbd5e1', lineHeight: 1.4 }}>
                        {selectedVidModel.description}
                      </div>
                    </div>
                  );
                })()}
              </>
            )}
          </div>

          <div className="help-text" style={{ marginBottom: 12 }}>
            Hệ thống ưu tiên biên câu trong khoảng 25–35 giây, rồi dùng ChatGPT qua
            Playwright để tạo visual bible và prompt riêng cho từng cảnh.
          </div>
          <label className="production-field-label">
            Style prompt
            <textarea
              className="prompt-textarea"
              rows={3}
              value={currentImageGeneration.style_prompt}
              onChange={event => handlePromptSettingChange(
                'image_generation_settings', 'style_prompt', event.target.value
              )}
              disabled={activeVersionLocked}
              placeholder="Phong cách hình ảnh dùng chung cho các scene"
            />
          </label>
          <label className="production-field-label">
            Negative prompt (Tránh tạo)
            <textarea
              className="prompt-textarea"
              rows={3}
              value={currentImageGeneration.negative_prompt}
              onChange={event => handlePromptSettingChange(
                'image_generation_settings', 'negative_prompt', event.target.value
              )}
              disabled={activeVersionLocked}
              placeholder="Các đặc điểm cần loại trừ"
            />
          </label>
        </div>
      )}

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>🎭 Thư viện nhân vật & bối cảnh tham chiếu (Reference Assets)</label>
            <div className="help-text" style={{ marginTop: 5 }}>
              Mỗi bộ prompt có một thư mục riêng. Đặt ảnh nhân vật/địa danh vào đây với tên file tương ứng (ví dụ: <code>bac_ba.png</code>, <code>chua_mot_cot.jpg</code>).
              Hệ thống tự động so khớp tên nhân vật với phụ đề từng cảnh để gửi ảnh mẫu vào Google Flow, giúp nhân vật luôn đồng nhất trong suốt video.
            </div>
          </div>
          <div className="prompt-header-actions">
            <button
              type="button"
              className="btn-secondary"
              onClick={handleOpenAssetsFolder}
              title={promptAssetsFolder ? `Đường dẫn: ${promptAssetsFolder}` : 'Mở thư mục'}
            >
              📁 Mở thư mục trên máy tính
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => fetchPromptAssets(activeVersion)}
              title="Quét lại các file ảnh mới thêm vào thư mục"
            >
              🔄 Làm mới
            </button>
          </div>
        </div>

        <div style={{ marginTop: 12 }}>
          {promptAssetsFolder && (
            <div style={{ fontSize: '0.8rem', color: '#888', marginBottom: 10, fontFamily: 'monospace', wordBreak: 'break-all' }}>
              📂 Đường dẫn thư mục: {promptAssetsFolder}
            </div>
          )}

          {assetMessage && (
            <div className="help-text" style={{ color: '#4ce0b3', marginBottom: 10, display: 'inline-block' }}>
              {assetMessage}
            </div>
          )}

          <div style={{ marginBottom: 15, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
            <label
              className="btn-secondary"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                cursor: activeVersionLocked ? 'not-allowed' : 'pointer',
                padding: '8px 16px',
                margin: 0,
                opacity: activeVersionLocked ? 0.6 : 1
              }}
            >
              {uploadingAsset ? '⏳ Đang tải lên...' : '➕ Tải thêm ảnh nhân vật / bối cảnh'}
              <input
                type="file"
                multiple
                accept="image/png, image/jpeg, image/webp"
                style={{ display: 'none' }}
                onChange={handleUploadAsset}
                disabled={activeVersionLocked || uploadingAsset}
              />
            </label>
            <span className="help-text">
              Chấp nhận .png, .jpg, .webp. Tên file nên viết không dấu hoặc gạch dưới (vd: <code>bac_ba.png</code>, <code>chi_lan.jpg</code>).
            </span>
          </div>

          {loadingAssets ? (
            <div style={{ color: '#888', padding: '16px 0' }}>Đang tải danh sách ảnh tham chiếu...</div>
          ) : promptAssets.length === 0 ? (
            <div style={{
              padding: '24px',
              textAlign: 'center',
              background: '#181818',
              borderRadius: '8px',
              border: '1px dashed #444',
              color: '#aaa'
            }}>
              <div style={{ fontSize: '2rem', marginBottom: 8 }}>🖼️</div>
              <div>Chưa có ảnh nhân vật hoặc bối cảnh nào trong bộ prompt <strong>{currentVersion?.name || activeVersion}</strong>.</div>
              <div style={{ fontSize: '0.85rem', color: '#777', marginTop: 6 }}>
                Nhấn <strong>"📁 Mở thư mục trên máy tính"</strong> để copy ảnh vào hoặc bấm <strong>"➕ Tải thêm ảnh..."</strong> ở trên.
              </div>
            </div>
          ) : (
            <div className="prompt-assets-grid">
              {promptAssets.map(asset => (
                <div key={asset.filename} className="prompt-asset-card">
                  <div className="prompt-asset-thumb-wrap">
                    <img
                      src={`http://127.0.0.1:8080/api/prompts/${encodeURIComponent(activeVersion)}/assets/${encodeURIComponent(asset.filename)}`}
                      alt={asset.display_name}
                      className="prompt-asset-thumb"
                      loading="lazy"
                    />
                    <button
                      type="button"
                      className="prompt-asset-delete-btn"
                      onClick={() => handleDeleteAsset(asset.filename)}
                      disabled={activeVersionLocked}
                      title={`Xóa ảnh ${asset.filename}`}
                    >
                      ✕
                    </button>
                  </div>
                  <div className="prompt-asset-info">
                    <div className="prompt-asset-name" title={asset.filename}>
                      {asset.filename}
                    </div>
                    <div className="prompt-asset-keywords" title={`Từ khóa nhận diện: ${asset.keywords?.join(', ')}`}>
                      🔑 {asset.keywords?.slice(0, 3).join(', ')}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {currentPipeline.youtube_upload && (
        <div className="prompt-item" style={{ marginBottom: '20px' }}>
          <div className="prompt-header">
            <div>
              <label>📤 Thiết lập upload YouTube của bộ prompt</label>
              <div className="help-text" style={{ marginTop: 5 }}>
                Video luôn được upload Private. Không có chế độ tự Public ngay.
              </div>
            </div>
            <button
              className="btn-save section-save-button"
              onClick={handleSavePublishing}
              disabled={Boolean(savingSection) || activeVersionLocked}
            >
              💾 Lưu thiết lập upload
            </button>
          </div>
          <div className="production-settings-grid">
            <label>
              Thumbnail dùng để upload
              <select
                className="version-select"
                value={currentImageGeneration.thumbnail_variant}
                onChange={event => handleThumbnailVariantChange(event.target.value)}
                disabled={activeVersionLocked}
              >
                <option value="without_text">Không chữ</option>
                <option value="with_text">Có chữ</option>
              </select>
            </label>
            <label>
              YouTube Category ID
              <input
                className="version-select"
                list="youtube-category-options"
                value={currentPublishing.category_id}
                onChange={event => handlePromptSettingChange(
                  'publishing_settings', 'category_id', event.target.value
                )}
                placeholder="Mặc định: 22 (People & Blogs)"
                disabled={activeVersionLocked}
              />
              <datalist id="youtube-category-options">
                {YOUTUBE_CATEGORIES.map(cat => (
                  <option key={cat.id} value={cat.id}>
                    {cat.name}
                  </option>
                ))}
              </datalist>
            </label>
            <label>
              Ngôn ngữ
              <input
                className="version-select"
                value={currentPublishing.language}
                onChange={event => handlePromptSettingChange(
                  'publishing_settings', 'language', event.target.value
                )}
                placeholder="vi"
                disabled={activeVersionLocked}
              />
            </label>
            <label>
              Dành cho trẻ em
              <select
                className="version-select"
                value={currentPublishing.made_for_kids === null ? '' : String(currentPublishing.made_for_kids)}
                onChange={event => handlePromptSettingChange(
                  'publishing_settings',
                  'made_for_kids',
                  event.target.value === '' ? null : event.target.value === 'true'
                )}
                disabled={activeVersionLocked}
              >
                <option value="">Bắt buộc chọn</option>
                <option value="false">Không dành cho trẻ em</option>
                <option value="true">Dành cho trẻ em</option>
              </select>
            </label>
          </div>
          <div className="help-text" style={{ marginTop: 8, fontSize: '0.84rem', color: '#94a3b8', lineHeight: 1.5 }}>
            💡 <strong>YouTube Category ID:</strong> Nếu để trống, hệ thống sẽ tự động dùng mặc định là <strong>22 (People & Blogs / Mọi người & Blog)</strong>. Bạn có thể chọn nhanh từ danh sách gợi ý hoặc nhập ID tùy chỉnh (VD: <code>22</code>: Blogs, <code>24</code>: Giải trí, <code>27</code>: Giáo dục, <code>28</code>: Khoa học & CN, <code>10</code>: Âm nhạc, <code>20</code>: Trò chơi, <code>1</code>: Phim).
          </div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 12 }}>
            <label>
              <input
                type="checkbox"
                checked={currentPublishing.notify_subscribers}
                onChange={event => handlePromptSettingChange(
                  'publishing_settings', 'notify_subscribers', event.target.checked
                )}
                disabled={activeVersionLocked}
              /> Thông báo người đăng ký khi video được công khai
            </label>
            <span style={{ color: '#4dd0e1' }}>✓ Luôn khai báo nội dung tổng hợp bằng AI</span>
          </div>
          <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid rgba(255, 255, 255, 0.1)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
              <label style={{ fontWeight: 600, color: '#fff', margin: 0 }}>
                📝 Mẫu mô tả YouTube thực tế (Description Template)
              </label>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {[
                  { tag: '{title}', label: '+ Tiêu đề' },
                  { tag: '{slug}', label: '+ Slug' },
                  { tag: '{description}', label: '+ Mô tả' },
                  { tag: '{tags}', label: '+ Tags' },
                  { tag: '{pinned_comment}', label: '+ Ghim' },
                  { tag: '{quiz}', label: '+ Quiz' },
                  { tag: '{chapters}', label: '+ Chapters' }
                ].map(item => (
                  <button
                    key={item.tag}
                    type="button"
                    className="btn-secondary"
                    style={{ padding: '2px 8px', fontSize: '0.75rem', borderRadius: '4px' }}
                    onClick={() => {
                      const currentTpl = currentPublishing.description_template || '';
                      const nextTpl = currentTpl ? `${currentTpl}\n\n${item.tag}` : item.tag;
                      handlePromptSettingChange('publishing_settings', 'description_template', nextTpl);
                    }}
                    disabled={activeVersionLocked}
                    title={`Chèn biến ${item.tag}`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="help-text" style={{ marginBottom: 8 }}>
              Tùy biến nội dung mô tả sẽ được dùng khi upload lên YouTube. Nhấp các nút trên để chèn nhanh biến động. Nếu để trống, hệ thống sẽ tự động ghép theo thứ tự mặc định: Mô tả → Chapters → Tags.
            </div>
            <textarea
              className="prompt-textarea"
              rows={6}
              value={currentPublishing.description_template || ''}
              onChange={event => handlePromptSettingChange(
                'publishing_settings', 'description_template', event.target.value
              )}
              placeholder="Ví dụ:\n{description}\n\n--- DANH SÁCH PHÂN ĐOẠN ---\n{chapters}\n\n--- TƯƠNG TÁC CÙNG KÊNH ---\n{pinned_comment}\n\n{quiz}\n\n{tags}"
              disabled={activeVersionLocked}
            />
          </div>
          {currentPipeline.youtube_schedule && (
            <div className="help-text" style={{ marginTop: 10, color: '#f5b041' }}>
              Lịch đăng, timezone, giới hạn ngày và nút pause được cấu hình tại phần kênh YouTube phía trên.
            </div>
          )}
        </div>
      )}

      {(loadingMsg || resultMsg) &&
        resultSection !== 'prompt-default-youtube-channel' && (
        <div className="status-box" style={{marginBottom: '20px'}}>
          {loadingMsg && <p className="loading">{loadingMsg}</p>}
          {resultMsg && <p className="result">{resultMsg}</p>}
        </div>
      )}

      <div className="prompts-list">
        {promptFields.map(field => (
          <div key={field.key} className="prompt-item">
            <div className="prompt-header">
              <label>{field.label}</label>
              <div className="prompt-header-actions">
                {field.help && <span className="help-text">{field.help}</span>}
                <button
                  className="btn-save section-save-button"
                  onClick={() => handleSavePrompt(field.key, field.label)}
                  disabled={Boolean(savingSection) || activeVersionLocked}
                >
                  💾 Lưu
                </button>
              </div>
            </div>
            <div style={{ display: 'flex', gap: '10px' }}>
              <textarea
                className="prompt-textarea"
                style={{ flex: 1 }}
                value={currentVersion.prompts[field.key] || ''}
                onChange={(e) => handlePromptChange(field.key, e.target.value)}
                rows={6}
                disabled={activeVersionLocked}
              />
              {(field.key === 'thumb_text' || field.key === 'thumb_notext') && (
                <div style={{ width: '150px', border: '1px dashed #666', borderRadius: '4px', padding: '10px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.2)' }}>
                  {renderImagePreviews(field.key)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
