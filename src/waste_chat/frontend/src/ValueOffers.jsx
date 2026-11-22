import {useState} from 'react';
import {Button} from '@astryxdesign/core/Button';

export function ValueOffers({offers,apply,dismiss,id}) {
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  if (!offers?.length) return null;
  async function choose(offer) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {await apply(offer.id);}
    catch (err) {setError(err.message || '현재 칸과 후보를 다시 확인하세요.');}
    finally {setBusy(false);}
  }
  return <section id={id} className="field-offers" aria-label="가져올 수 있는 값">
    <h3>가져올 수 있는 값</h3>
    {offers.map(offer=><div key={offer.id} data-offer-id={offer.id} className="field-offer">
      <strong className="offer-value">{offer.value}</strong>
      <span className="offer-source">출처 · {offer.source}</span>
      <div className="offer-actions">
        <Button size="sm" variant="primary" className="approve-offer" label="이 값 반영" isDisabled={busy} onClick={()=>choose(offer)}/>
        <Button size="sm" variant="ghost" className="dismiss-offer" label="후보 제외" isDisabled={busy} onClick={()=>{setError('');dismiss(offer.id);}}/>
      </div>
    </div>)}
    {error && <p role="alert" className="offer-issue">{error}</p>}
  </section>;
}
