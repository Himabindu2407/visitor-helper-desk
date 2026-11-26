(() => {
 const form=document.getElementById('visitorForm');
 const csrfToken=document.getElementById('publicCsrf')?.value||'';
 const msg=document.getElementById('formMessage');
 const successPanel=document.getElementById('successPanel');
 const submit=form.querySelector('.submit');
 const ids=['fullName','mobile','visitorType','organization','email','personToMeet','purpose'];
 const openCameraBtn=document.getElementById('openCamera'), cameraPanel=document.getElementById('cameraPanel');
 const video=document.getElementById('selfieVideo'), previewWrap=document.getElementById('selfiePreviewWrap');
 const preview=document.getElementById('selfiePreview'), selfieStatus=document.getElementById('selfieStatus');
 const selfieError=document.getElementById('selfieError'), captureBtn=document.getElementById('captureSelfie');
 const closeBtn=document.getElementById('closeCamera'), retakeBtn=document.getElementById('retakeSelfie');
 const removeBtn=document.getElementById('removeSelfie');
 const selfieInput=document.getElementById('selfieInput');
 let cameraStream=null, selfieBlob=null, previewUrl=null;
 const clearErrors=()=>ids.forEach(id=>{const e=document.getElementById(id+'Error');if(e)e.textContent='';});
 const values=()=>Object.fromEntries([...ids,'notes'].map(id=>[id,document.getElementById(id).value.trim()]));
 function validate(){
  clearErrors();let valid=true;const v=values();
  const err=(id,t)=>{const e=document.getElementById(id+'Error');if(e){e.textContent=t;valid=false;}};
  if(v.fullName.length<2)err('fullName','Please enter your full name.');
  if(!/^[6-9]\d{9}$/.test(v.mobile))err('mobile','Enter a valid 10-digit Indian mobile number.');
  if(!v.visitorType)err('visitorType','Please select visitor type.');
  if(v.email&&!/^\S+@\S+\.\S+$/.test(v.email))err('email','Please enter a valid email address.');
  if(v.personToMeet.length<2)err('personToMeet','Enter the person or department to meet.');
  if(!v.purpose)err('purpose','Please enter your purpose of visit.');
  if(!selfieBlob){selfieError.textContent='Please upload a selfie before registering.';valid=false;}
  return valid;
 }
 function stopCamera(){if(cameraStream){cameraStream.getTracks().forEach(t=>t.stop());cameraStream=null;}video.srcObject=null;}
 async function startCamera(){
  selfieError.textContent='';
  if(!navigator.mediaDevices?.getUserMedia){selfieError.textContent='Desktop webcam requires HTTPS or localhost. Open the secure website and try again.';return;}
  try{stopCamera();cameraStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'user',width:{ideal:1280},height:{ideal:720}},audio:false});video.srcObject=cameraStream;cameraPanel.hidden=false;previewWrap.hidden=true;await video.play();selfieStatus.textContent='Camera is ready. Take your selfie.';}
  catch(error){stopCamera();selfieError.textContent=error.name==='NotAllowedError'?'Camera permission was not granted. Allow camera access in browser settings and try again.':'Unable to open the camera. Check camera access and try again.';}
 }
 function chooseSelfie(){
  selfieError.textContent='';
  selfieInput.value='';
  selfieInput.click();
 }
 selfieInput.addEventListener('change',async()=>{
  const file=selfieInput.files?.[0];if(!file)return;
  if(!['image/jpeg','image/png','image/webp'].includes(file.type)){selfieError.textContent='Please select a JPG, PNG, or WEBP selfie.';return;}
  if(!file.size||file.size>20*1024*1024){selfieError.textContent='Choose a non-empty selfie under 20 MB.';return;}
  submit.disabled=true;
  try {
   const bitmap=await createImageBitmap(file); const canvas=document.createElement('canvas');
   const scale=Math.min(1,1600/Math.max(bitmap.width,bitmap.height));
   canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
   canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close();
   const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',0.85));
   if(!blob||blob.size>3*1024*1024)throw new Error('Selfie could not be compressed.');
   selfieBlob=blob;
  } catch(err){selfieError.textContent='Unable to prepare selfie. Try another photo.';return;}
  finally{submit.disabled=false;}
  stopCamera();cameraPanel.hidden=true;
  if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=URL.createObjectURL(selfieBlob);preview.src=previewUrl;
  previewWrap.hidden=false;selfieStatus.textContent='Selfie selected. It will be submitted with your registration.';selfieError.textContent='';
 });
 function openSelfie(){
  const mobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  if(mobile){chooseSelfie();return;}
  startCamera();
 }
 openCameraBtn.addEventListener('click',openSelfie);
 closeBtn.addEventListener('click',()=>{stopCamera();cameraPanel.hidden=true;});
 captureBtn.addEventListener('click',()=>{
  if(!video.videoWidth){selfieError.textContent='Camera is not ready yet. Please wait.';return;}
  const canvas=document.createElement('canvas');canvas.width=video.videoWidth;canvas.height=video.videoHeight;
  const ctx=canvas.getContext('2d');ctx.translate(canvas.width,0);ctx.scale(-1,1);ctx.drawImage(video,0,0);
  canvas.toBlob(blob=>{if(!blob){selfieError.textContent='Could not capture selfie. Please try again.';return;}
   selfieBlob=blob;if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=URL.createObjectURL(blob);preview.src=previewUrl;
   previewWrap.hidden=false;cameraPanel.hidden=true;stopCamera();selfieStatus.textContent='Selfie captured. It will be submitted with your registration.';selfieError.textContent='';
  },'image/jpeg',0.88);
 });
 retakeBtn.addEventListener('click',openSelfie);
 removeBtn.addEventListener('click',()=>{selfieInput.value='';selfieBlob=null;preview.removeAttribute('src');previewWrap.hidden=true;if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=null;selfieStatus.textContent='No selfie selected. A selfie is required.';});
 form.addEventListener('submit',async e=>{
  e.preventDefault();msg.textContent='';msg.style.color='#c80000';
  if(!validate()){msg.textContent='Please correct the highlighted fields.';return;}
  submit.disabled=true;submit.textContent='Registering…';
  try{
   const body=new FormData();Object.entries(values()).forEach(([k,v])=>body.append(k,v));
   body.append('selfie',selfieBlob,selfieBlob.name||'visitor-selfie.jpg');
   const r=await fetch('/api/register',{method:'POST',headers:{'X-CSRF-Token':csrfToken},body});
   const d=await r.json();if(!r.ok)throw new Error(d.message||'Registration failed. Please try again.');
   stopCamera();form.classList.add('success-mode');successPanel.hidden=false;successPanel.scrollIntoView({behavior:'smooth',block:'center'});
  }catch(err){msg.textContent=err.message;submit.disabled=false;submit.innerHTML='Register <span>→</span>';}
 });
 window.addEventListener('beforeunload',stopCamera);
})();