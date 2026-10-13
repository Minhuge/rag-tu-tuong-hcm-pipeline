import { useParams } from 'react-router'

/** id trên URL phải là số nguyên dương; sai → null (trang hiện "không tìm thấy" thay vì gọi API với id rác). */
export function useNumericId() {
  const { id } = useParams()
  return /^[1-9]\d*$/.test(id ?? '') ? Number(id) : null
}
