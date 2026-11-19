import { useState } from 'react';
import { LeadsTable } from './Leads.jsx';
import { Link } from 'react-router-dom';
import { api } from '../api.js';
import { useAction, useResource, useWorkspacePath } from '../hooks.js';
import { ErrorNotice, PageHeading } from '../components.jsx';

const blank = { name: '', description: '', price: '', currency: '', image_url: '', features: '', specifications: '', availability: 'unknown', warranty: '', additional_info: '', active: true };

function ProductEditor({ base, product, close }) {
  const { busy, error, run } = useAction();
  const [faq, setFaq] = useState({ question: '', answer: '' });
  const [formError, setFormError] = useState('');
  const initial = product ? { ...product, features: product.features.join('\n'), specifications: JSON.stringify(product.specifications, null, 2) } : blank;
  async function save(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    let specifications;
    try { specifications = JSON.parse(form.get('specifications') || '{}'); }
    catch { setFormError('Specifications must be a valid JSON object.'); return; }
    setFormError('');
    const data = Object.fromEntries(form.entries());
    data.active = form.has('active');
    data.features = String(data.features || '').split('\n').map(value => value.trim()).filter(Boolean);
    data.specifications = specifications;
    data.price = data.price || null;
    const result = await run(async () => {
      const response = product
        ? await api.patch(`${base}products/${product.id}/`, data)
        : await api.post(`${base}products/`, data);
      return response.data;
    });
    if (result) close();
  }
  return <section className="panel p-5 space-y-3"><h2>{product ? `Edit ${product.name}` : 'Add a product'}</h2><ErrorNotice>{error || formError}</ErrorNotice>
    <form className="grid gap-3 md:grid-cols-2" onSubmit={save}>
      <label>Product name<input name="name" required maxLength="160" defaultValue={initial.name} /></label>
      <label>Availability<select name="availability" defaultValue={initial.availability}><option value="unknown">Check with business</option><option value="in_stock">In stock</option><option value="out_of_stock">Out of stock</option><option value="preorder">Preorder</option></select></label>
      <label>Description<textarea name="description" maxLength="6000" defaultValue={initial.description} /></label>
      <label>Product image HTTPS URL<input name="image_url" type="url" placeholder="https://…" defaultValue={initial.image_url} /></label>
      <label>Price<input name="price" type="number" min="0" step="0.01" defaultValue={initial.price ?? ''} /></label>
      <label>Currency code<input name="currency" maxLength="3" placeholder="USD" defaultValue={initial.currency} /></label>
      <label>Features, one per line<textarea name="features" defaultValue={initial.features} /></label>
      <label>Specifications, JSON object<textarea name="specifications" defaultValue={initial.specifications} /></label>
      <label>Warranty<input name="warranty" maxLength="1000" defaultValue={initial.warranty} /></label>
      <label>Additional product information<textarea name="additional_info" maxLength="12000" defaultValue={initial.additional_info} /></label>
      <label className="flex items-center gap-2"><input name="active" type="checkbox" defaultChecked={initial.active} /> Active and available to the assistant</label>
      <div className="flex gap-2"><button className="btn primary" disabled={busy}>Save product</button><button type="button" className="btn ghost" onClick={close}>Cancel</button></div>
    </form>
    {product && <div className="border-t pt-4 space-y-3"><h3>Product FAQs</h3>
      {product.faqs.map(item => <div key={item.id} className="flex justify-between border-b py-2"><div><strong>{item.question}</strong><p>{item.answer}</p><small>{item.active ? 'Active' : 'Inactive'}</small></div><button className="btn ghost compact" disabled={busy} onClick={() => run(() => api.delete(`${base}product-faqs/${item.id}/`))}>Delete</button></div>)}
      <form className="grid gap-2" onSubmit={event => { event.preventDefault(); run(async () => { await api.post(`${base}product-faqs/`, { product: product.id, ...faq }); setFaq({ question: '', answer: '' }); }); }}>
        <label>Question<input required maxLength="240" value={faq.question} onChange={event => setFaq({ ...faq, question: event.target.value })} /></label>
        <label>Approved answer<textarea required maxLength="3000" value={faq.answer} onChange={event => setFaq({ ...faq, answer: event.target.value })} /></label><button className="btn secondary" disabled={busy}>Add FAQ</button>
      </form>
      <h3>Product documents</h3>{product.documents.map(item => <div key={item.id} className="flex justify-between"><span>{item.title}</span><button className="btn ghost compact" disabled={busy} onClick={() => run(() => api.delete(`${base}product-documents/${item.id}/`))}>Delete</button></div>)}
      <form onSubmit={event => { event.preventDefault(); const file = event.currentTarget.elements.file.files[0]; if (!file) return; const body = new FormData(); body.append('file', file); run(() => api.post(`${base}products/${product.id}/documents/`, body)); event.currentTarget.reset(); }}>
        <label>Upload product notes (.txt or .md, up to 20 KB)<input name="file" type="file" accept=".txt,.md,text/plain,text/markdown" /></label><button className="btn secondary" disabled={busy}>Add document</button>
      </form>
      <p className="muted text-sm">Uploaded notes are UTF-8 text, stored with this product, and included only when this product is relevant.</p>
    </div>}
  </section>;
}

function ProductsTab({ base }) {
  const products = useResource(`${base}products/`);
  const [editing, setEditing] = useState(undefined);
  const { busy, error, run } = useAction();
  return <><ErrorNotice>{products.error || error}</ErrorNotice><div className="grid gap-5 lg:grid-cols-2">
    <section className="panel p-5 space-y-3"><div className="flex justify-between"><h2>Products</h2><button className="btn primary" onClick={() => setEditing(null)}>Add product</button></div>
      {products.data?.results?.map(item => <article className="border-t py-4" key={item.id}>{item.image_url && <img src={item.image_url} alt="" loading="lazy" className="h-32 w-full rounded object-cover" referrerPolicy="no-referrer" />}<h3>{item.name} {!item.active && <small>(inactive)</small>}</h3><p>{item.description}</p><p>{item.price == null ? 'Price not configured' : `${item.price} ${item.currency}`}</p><p>{item.availability.replaceAll('_', ' ')}</p><p className="muted">{item.faqs.length} FAQs · {item.documents.length} documents</p><button className="btn secondary compact" onClick={() => setEditing(item)}>Edit</button><button className="btn ghost compact" disabled={busy} onClick={() => { if (window.confirm(`Delete ${item.name}? Its FAQs and documents will also be removed.`)) run(() => api.delete(`${base}products/${item.id}/`)); }}>Delete</button></article>)}
      {!products.loading && !products.data?.results?.length && <p className="muted">No products yet. Add the facts customers can ask about.</p>}
    </section>{editing !== undefined && <ProductEditor key={editing?.id || 'new'} base={base} product={editing} close={() => { setEditing(undefined); }} />}
  </div></>;
}

// The Enquiries tab and the Leads page share one component and one API.
const LeadsTab = LeadsTable;

export default function Products() {
  const base = useWorkspacePath();
  const [tab, setTab] = useState('products');
  return <><PageHeading title="Products and leads" action={<Link to="/chatbot" className="btn secondary">AI chatbot settings</Link>}>Manage product facts used by the website assistant and enquiries from this workspace.</PageHeading>
    <div className="tabs mb-4"><button className={tab === 'products' ? 'active' : ''} onClick={() => setTab('products')}>Products</button><button className={tab === 'leads' ? 'active' : ''} onClick={() => setTab('leads')}>Enquiries</button></div>
    {tab === 'products' ? <ProductsTab base={base} /> : <LeadsTab base={base} />}
  </>;
}
